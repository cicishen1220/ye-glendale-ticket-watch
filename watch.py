"""One cloud check of the public StubHub event page. Prices are integer cents."""
import json
import os
import re
import smtplib
import ssl
import time
from datetime import datetime, timezone
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

EVENT = "https://www.stubhub.com/ye-glendale-tickets-11-21-2026/event/161990980"
SECTIONS = set(range(105, 113)) | set(range(126, 134))
MAP_IDS = list(range(191978, 191986)) + list(range(191999, 192007))
URL = EVENT + "?" + urlencode(dict(quantity=0, sortDirection=0, sortBy="NEWPRICE",
                                   sections=",".join(map(str, MAP_IDS))))
END = datetime(2026, 11, 22, 3, 30, tzinfo=timezone.utc)
STATE = Path("monitor-state.json")
FEE = re.compile(r"^(?:(?:includes?|including|incl\.?) (?:all )?fees|(?:all )?fees included|含费用)[.!]?$", re.I | re.M)
CARD = '[role="button"][data-listing-id][data-price]'
MORE = re.compile(r"^(?:show|load|see|view) more(?: listings| tickets)?$|^显示更多$", re.I)
QTY = re.compile(r"^(?:\d+ tickets?|any(?: quantity)?|\d+ 张票|任何)\b", re.I)
ANY = re.compile(r"^any(?: quantity)?$|^任何$", re.I)
SECTION_IDS = dict(zip(sorted(SECTIONS), MAP_IDS))


def section_link(section):
    return EVENT + "?" + urlencode(dict(sections=SECTION_IDS[section], quantity=0,
                                        sortDirection=0, sortBy="NEWPRICE"))


def qualifying(cards):
    matches = {}
    for card in cards:
        if card["sold"] == "1":
            continue
        text = card["text"]
        section = re.search(r"^(?:Section\s+(\d+)\s*|(\d+)\s*区)\s*$", text, re.I | re.M)
        if not section:
            raise ValueError("Could not read a section in the filtered ticket list")
        if int(section[1] or section[2]) not in SECTIONS:
            continue
        price = re.fullmatch(r"(?:US)?\$\s*([\d,]+(?:\.\d{1,2})?)", card["price"])
        if not price or not FEE.search(text):
            raise ValueError("Could not verify the USD price including fees")
        cents = int(Decimal(price[1].replace(",", "")) * 100)
        if 0 < cents < 70000:
            row = re.search(r"^(?:Row\s+|第\s*).+$", text, re.I | re.M)
            qty = re.search(r"^[^\n]*(?:\btickets?\b[^\n]*|张(?:连座)?票[^\n]*)$", text, re.I | re.M)
            matches[card["id"]] = dict(cents=cents, section=int(section[1] or section[2]),
                                        row=row[0].strip() if row else "排号未显示",
                                        qty=qty[0].strip() if qty else "张数未显示")
    return matches


def read_view(browser, locale, width):
    from playwright.sync_api import expect
    with browser.new_context(locale=locale, timezone_id="America/Phoenix",
                             viewport={"width": width, "height": 900}) as context:
        page = context.new_page()
        page.set_default_timeout(25000)
        page.goto(URL, wait_until="domcontentloaded")
        cards = page.locator(CARD)
        go = page.get_by_role("button", name=re.compile(r"^Continue$|^继续$", re.I))
        try:
            go.or_(cards).filter(visible=True).first.wait_for(state="visible", timeout=45000)
        except Exception as exc:
            if any("captcha-delivery.com" in urlparse(frame.url).netloc for frame in page.frames):
                raise RuntimeError("StubHub requested device verification on the cloud runner") from exc
            raise RuntimeError("The event page did not show the ticket selector or listings") from exc
        if go.is_visible():
            go.click()
            go.wait_for(state="hidden")
        qty = page.get_by_role("button", name=QTY).or_(
            page.get_by_role("combobox").filter(has_text=QTY)).filter(visible=True)
        qty.first.wait_for(state="visible")
        if not ANY.fullmatch(qty.first.inner_text().strip()):
            qty.first.click()
            page.get_by_text(ANY).filter(visible=True).first.click()
        expect(qty.first).to_contain_text(ANY)
        cards.first.wait_for(state="visible")
        page.wait_for_url(lambda url: parse_qs(urlparse(url).query).get("quantity") == ["0"])
        params = parse_qs(urlparse(page.url).query)
        if (params.get("quantity") != ["0"] or params.get("sortBy") != ["NEWPRICE"]
                or params.get("sortDirection") != ["0"]
                or set(params.get("sections", [""])[0].split(",")) != set(map(str, MAP_IDS))):
            raise ValueError("The section or quantity filters were lost")
        more = page.get_by_role("button", name=MORE)
        found, inspected = {}, set()
        for _ in range(150):
            def snapshot():
                return cards.evaluate_all("""cards => cards.map(c => ({id: c.dataset.listingId,
                    price: [...c.querySelectorAll('h3, [role="heading"]')].map(h => h.innerText.trim())
                          .find(t => /^(?:US)?\\$/.test(t)) || c.dataset.price,
                    sold: c.dataset.isSold, text: c.innerText,
                    label: c.getAttribute('aria-label') || ''}))""")
            rows = snapshot()
            # The quantity changes before the site replaces the previous list.
            for _attempt in range(6):
                time.sleep(0.6)
                updated = snapshot()
                if updated == rows and updated and _attempt >= 1:
                    break
                rows = updated
            else:
                raise ValueError("The listing results did not settle")
            if not rows:
                raise ValueError("StubHub did not return verifiable listings")
            found.update(qualifying(rows))
            inspected.update(row["id"] for row in rows)
            organic = [row for row in rows if row["sold"] != "1" and not
                       re.search(r"sponsored|已赞助", row["label"] + "\n" + row["text"], re.I)]
            prices = [Decimal(row["price"].replace("US", "").replace("$", "").replace(",", "")) for row in organic]
            for row in rows:
                if re.search(r"^(Section 110|110\s*区)\s*$", row["text"], re.M | re.I):
                    print(f"{locale} Section 110: {row['id']} {row['price']}")
            if prices and prices == sorted(prices) and prices[-1] >= 700:
                print(f"{locale}: regular listings from USD {prices[0]} through {prices[-1]}.")
                return found, inspected
            if not more.is_visible():
                counts = re.search(r"showing\s+([\d,]+)\s+(?:of|out of)\s+([\d,]+)|显示\s*([\d,]+)\s*个[（(]共\s*([\d,]+)\s*个", page.locator("body").inner_text(), re.I)
                if counts:
                    shown, total = [int(n.replace(",", "")) for n in counts.groups() if n]
                    if shown < total or len(inspected) < total:
                        raise ValueError("The listing count indicates an incomplete page")
                return found, inspected
            more.click()
            page.wait_for_function("""({sel, seen}) => Array.from(document.querySelectorAll(sel))
                .some(card => !seen.includes(card.dataset.listingId))""",
                                   arg={"sel": CARD, "seen": list(inspected)})
        raise ValueError("Could not finish pagination")


def read_page():
    from playwright.sync_api import sync_playwright
    found, inspected, errors = {}, set(), []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        for locale, width in (("en-US", 1280), ("zh-CN", 850)):
            try:
                items, ids = read_view(browser, locale, width)
            except Exception as exc:
                errors.append(f"{locale}: {type(exc).__name__}: {str(exc)[:400]}")
                continue
            inspected.update(ids)
            for key, item in items.items():
                if key not in found or item["cents"] < found[key]["cents"]:
                    found[key] = item
    if errors:
        raise IncompleteCheck(found, inspected, errors)
    return found, len(inspected)


class IncompleteCheck(RuntimeError):
    def __init__(self, found, inspected, errors):
        super().__init__("; ".join(errors))
        self.found, self.total = found, len(inspected)


def send_mail(subject, body):
    mailbox = os.environ["ALERT_EMAIL"]
    msg = EmailMessage()
    msg["From"] = msg["To"] = mailbox
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=25) as client:
        client.login(mailbox, os.environ["GMAIL_APP_PASSWORD"].replace(" ", ""))
        client.send_message(msg)


def save(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True) + "\n")
    tmp.replace(STATE)


def run():
    if datetime.now(timezone.utc) >= END:
        return
    if not all(os.getenv(k) for k in ("ALERT_EMAIL", "GMAIL_APP_PASSWORD")):
        found, total = read_page()
        print(f"Live check succeeded: {total} listings, {len(found)} below the threshold.")
        raise RuntimeError("Email is pending. Configure ALERT_EMAIL and GMAIL_APP_PASSWORD to activate alerts.")
    state = json.loads(STATE.read_text()) if STATE.exists() else {"active": {}, "ready": False, "failures": 0}
    incomplete = None
    try:
        found, total = read_page()
    except IncompleteCheck as exc:
        found, total, incomplete = exc.found, exc.total, exc
    except Exception as exc:
        found, total, incomplete = {}, 0, exc
    if datetime.now(timezone.utc) >= END:
        return
    active = state["active"]
    new = [(i, item) for i, item in found.items() if i not in active or item["cents"] < active[i]]
    new.sort(key=lambda pair: pair[1]["cents"])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if new:
        lines = [f"{item['section']} 区 · {item['row']} · {item['qty']} · 含费 ${item['cents']/100:.2f}/张（票单 {i}）\n{section_link(item['section'])}" for i, item in new]
        send_mail(f"Ye Glendale｜{len(new)} 条低于 700 美元的票单", "\n".join([ts, *lines, "", URL]))
        for i, item in new:
            print(f"Email accepted: listing {i}, Section {item['section']}, USD {item['cents']/100:.2f}.")
    elif not incomplete and not state["ready"]:
        send_mail("Ye Glendale 云端查票已开始", f"{ts}，首次云端检查成功。已读取 {total} 条票单，指定区目前没有低于 700 美元的票。\n{URL}")
    if incomplete:
        state["active"].update({i: item["cents"] for i, item in new})
        if new:
            state["ready"] = True
        state["failures"] = min(state["failures"] + 1, 3)
        save(state)
        if state["failures"] == 3 and not state.get("warned"):
            send_mail("Ye Glendale 查票暂时中断", "连续三次未能完整核实 StubHub 现场票单。请查看 GitHub Actions 运行记录。\n" + URL)
            state["warned"] = True
            save(state)
        raise incomplete
    updated = {"ready": True, "failures": 0, "active": {i: item["cents"] for i, item in found.items()}}
    if state != updated:
        save(updated)
    print(f"Checked {total} listings; {len(found)} below threshold; {len(new)} new or reduced.")


if __name__ == "__main__":
    run()
