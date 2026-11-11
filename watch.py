"""One cloud check of the public StubHub event page. Prices are integer cents."""
import json
import os
import re
import smtplib
import ssl
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
            qty = re.search(r"^.*(?:\btickets?\b|张票)\s*$", text, re.I | re.M)
            matches[card["id"]] = dict(cents=cents, section=int(section[1] or section[2]),
                                        row=row[0].strip() if row else "排号未显示",
                                        qty=qty[0].strip() if qty else "张数未显示")
    return matches


def read_page():
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        page = browser.new_page(locale="en-US", timezone_id="America/Phoenix")
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
        qty = page.locator('button[aria-haspopup="listbox"]').filter(has_text=QTY)
        # Some versions use a generic popup button without aria-haspopup=listbox.
        if not qty.count():
            qty = page.get_by_role("button", name=QTY)
        qty.first.wait_for(state="visible")
        if not ANY.fullmatch(qty.first.inner_text().strip()):
            qty.first.click()
            page.locator('li[role="option"]:visible').filter(has_text=ANY).click()
        any_button = page.get_by_role("button", name=re.compile(r"^(?:any\b|任何)", re.I))
        expect(any_button.first).to_be_enabled()
        cards.first.wait_for(state="visible")
        params = parse_qs(urlparse(page.url).query)
        if params.get("quantity") != ["0"] or set(params.get("sections", [""])[0].split(",")) != set(map(str, MAP_IDS)):
            raise ValueError("The section or quantity filters were lost")
        more = page.get_by_role("button", name=MORE)
        for _ in range(150):
            if not more.is_visible():
                break
            previous_count = cards.count()
            more.click()
            page.wait_for_function("(arg) => document.querySelectorAll(arg.sel).length > arg.n",
                                   arg={"sel": CARD, "n": previous_count})
        else:
            raise ValueError("Could not finish pagination")
        counts = re.search(r"(?:showing\s+([\d,]+)\s+(?:of|out of)\s+([\d,]+)|显示\s*([\d,]+)\s*个[（(]共\s*([\d,]+)\s*个)", page.locator("body").inner_text(), re.I)
        if counts:
            values = [int(n.replace(",", "")) for n in counts.groups() if n]
            if values[0] != values[1] or cards.count() < values[0]:
                raise ValueError("The listing count indicates an incomplete page")
        rows = cards.evaluate_all("""cards => cards.map(c => ({id: c.dataset.listingId,
            price: c.dataset.price, sold: c.dataset.isSold, text: c.innerText}))""")
        if not rows:
            raise ValueError("StubHub did not return verifiable listings")
        return qualifying(rows), len(rows)


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
    try:
        found, total = read_page()
    except Exception:
        state["failures"] = min(state["failures"] + 1, 3)
        if state["failures"] == 3 and not state.get("warned"):
            send_mail("Ye Glendale 查票暂时中断", "连续三次未能核实 StubHub 现场票单。请查看 GitHub Actions 运行记录。\n" + URL)
            state["warned"] = True
        save(state)
        raise
    if datetime.now(timezone.utc) >= END:
        return
    active = state["active"]
    new = [(i, item) for i, item in found.items() if i not in active or item["cents"] < active[i]]
    new.sort(key=lambda pair: pair[1]["cents"])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if new:
        lines = [f"{item['section']} 区 · {item['row']} · {item['qty']} · 含费 ${item['cents']/100:.2f}/张（票单 {i}）" for i, item in new]
        send_mail(f"Ye Glendale｜{len(new)} 条低于 700 美元的票单", "\n".join([ts, *lines, "", URL]))
    elif not state["ready"]:
        send_mail("Ye Glendale 云端查票已开始", f"{ts}，首次云端检查成功。已读取 {total} 条票单，指定区目前没有低于 700 美元的票。\n{URL}")
    updated = {"ready": True, "failures": 0, "active": {i: item["cents"] for i, item in found.items()}}
    if state != updated:
        save(updated)
    print(f"Checked {total} listings; {len(found)} below threshold; {len(new)} new or reduced.")


if __name__ == "__main__":
    run()
