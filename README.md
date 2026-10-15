# JobPing

When a plumbing shop misses a call, JobPing texts the caller back within a minute, offers three open times from the owner's calendar, books the job, alerts the owner, reminds the customer the day before, and asks for a review after the job.

This is a **working prototype** of the "minimum product" from the launch plan. It runs end to end in a simulator with fake calls and fake texts. It has not been connected to real phone lines yet. Do that only after the go-live steps below.

## Try it on your own computer

You need Python 3.10 or newer from [python.org](https://www.python.org/downloads/). On Windows, tick **Add python.exe to PATH** in the installer.

- **Windows:** double-click `run-demo.bat`.
- **Mac or Linux:** run `./run-demo.sh`.

Your browser opens to the login page. Use the email and password the window prints (a made-up demo shop, "Lakeshore Plumbing & Drain", with six fake customers already on the board). Everything stays on your computer. No texts go out.

### A good first test (the Simulator page)

1. Press **Call, nobody answers**. The fake customer gets a text.
2. Reply as the customer: `kitchen faucet is leaking, 123 Main St`. They get three open times.
3. Reply `1`. The job is booked and your phone gets an alert.
4. Press **Jump 1 day** twice. The customer gets the day-before reminder.
5. Try `my basement is flooding` from a different number. The assistant stops and alerts you.
6. Try `STOP`. The customer gets no more texts.

## Three ways to run it (Setup > How should the assistant work?)

| Mode | What happens on a missed call |
|---|---|
| Tell me only | Nothing is ever sent to the caller. You get a text with their number. |
| Ask me first | You get a text with a link. One tap sends the text-back; when the customer picks a time you tap once to approve it (or say you can't make it and they are offered other times). |
| Fully automatic | Texts back within a minute and books open times by itself. |

Also in Setup: "Leave my past customers to me" (past customers get you an alert, never a robot text). On the Board, "No new bookings today / tomorrow" blocks new offers without touching booked jobs. `/report` shows the last 30 days of missed calls and what became of them.

## What it does, and the rules it never breaks

| Rule | How it is enforced |
|---|---|
| Never quotes a price | The assistant has no prices. Price words send the owner an alert and the customer is offered a visit. |
| Never promises a time the owner hasn't opened | Offers come only from the owner's hours minus booked jobs. A time is re-checked at the moment of booking, and the database refuses two jobs at the same start time. |
| Never handles an emergency | Flood, burst, gas, sewage and similar words stop the assistant and alert the owner. It leans toward false alarms on purpose. |
| Stops when someone says STOP | STOP, STOPALL, UNSUBSCRIBE, CANCEL, END and QUIT end all texts, including the owner's manual texts. HELP gets the required reply. |
| Says it is automated | Every first text names the business, says "automated assistant", and shows how to opt out. |

Every one of these has automated tests (153 of them: `python -m unittest discover -s tests -t .`).

## How a call flows

1. A customer calls the shop's Twilio number. Twilio asks the app what to do (`/hooks/twilio/voice`), and the app rings the owner's cell for 20 seconds.
2. When the owner's phone connects, a short message plays: "Press any key to answer." A person presses a key, so the call counts as answered. Voicemail cannot press a key, so a call that lands in the owner's voicemail still counts as missed. This is on by default (`SCREEN_CALLS=1`).
3. If nobody took the call, the app texts the caller. The customer replies with the problem and gets three times. A number reply books it. The same link also opens a booking page (`/book/<private link>`).
4. The owner gets an alert text for every booking and every hand-off, and sees everything on the Board (`/dashboard`).
5. A background check every minute sends reminders (5 PM the day before, never before 8 AM or after 9 PM) and review requests (2 hours after the owner marks the job done, 9 AM to 8 PM, only if a review link is set).

Calendar: Setup shows a private link. Add it to Google, Apple or Outlook Calendar and booked jobs appear on the owner's phone.

## Safety limits that protect your bill

| Limit | Default | Setting |
|---|---|---|
| Texts to one number per day | 6 | `MAX_TEXTS_PER_NUMBER_PER_DAY` |
| Automatic texts per business per day | 200 | `MAX_AUTO_TEXTS_PER_BUSINESS_PER_DAY` |
| "Sorry we missed your call" texts per business per day | 40 | `MAX_FIRST_TEXTS_PER_BUSINESS_PER_DAY` |
| Owner alerts per business per day | 30 | `MAX_OWNER_ALERTS_PER_BUSINESS_PER_DAY` |
| Same caller, repeat missed calls | one text per 2 hours | `MISSED_CALL_DEDUPE_MINUTES` |
| Texts to numbers outside the US and Canada | blocked | `ALLOWED_PREFIX` |
| Master off switch for all texts | on | `SMS_ENABLED=0` |

Blocked texts are still recorded with the reason, so nothing disappears quietly. The owner can also pause the assistant in Setup.

## Going live (do these in order)

Nothing below happens until you do it. Cost and legal items are marked.

1. **Texting registration (do this first, it takes days).** US carriers require businesses to register before they send texts from a regular number. In Twilio this is called A2P 10DLC registration (or toll-free verification). Registration has fees; read Twilio's current registration page before you start. Use the plan's rule: pay nothing until a second shop owner says yes.
2. **Twilio account and number.** Create a Twilio account and buy one local number (about $1.15 a month per Twilio's price page, checked October 2026). In the Twilio console, under Geo permissions, allow **only the United States and Canada** for both voice and SMS. This is your strongest protection against fraud.
3. **Spending limits.** In Twilio, turn off auto-recharge until you trust the traffic, and set a usage alert.
4. **Host the app** somewhere that gives it a **persistent disk** (the data is one SQLite file; on a host without a disk it is erased on every restart) and a web address that starts with `https://`. Run it with the `Procfile` (`gunicorn ... --workers 1 --threads 4`). Keep it at one worker: the in-memory limits and the minute-by-minute scheduler assume it.
5. **Settings.** Enter these as environment variables on your host's settings page (`.env.example` lists them all; the app does not read a `.env` file). Set at least `SECRET_KEY` (a long random string), `BASE_URL`, `DATABASE_PATH` (on the persistent disk), `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `SMS_PROVIDER=twilio`, `SIMULATOR=0` and `TRUST_PROXY=1` if the host puts a proxy in front. The app **refuses to start** with live texting if the key is short, Twilio details are missing, the address isn't https, or the simulator is on.
6. **Create the shop's account yourself.** Open sign-up is off when texting is live (otherwise a stranger could point calls and texts at your Twilio bill). Run:
   `flask --app wsgi create-business --name "Hall Plumbing" --email owner@example.com --cell "(586) 555-0100" --text-number +15865550123`
   The owner then logs in and sets hours in Setup. Owners cannot change the texting number.
7. **Point Twilio at the app.** On the Twilio number: "A call comes in" → Webhook, `POST`, `https://YOUR-ADDRESS/hooks/twilio/voice`. "A message comes in" → Webhook, `POST`, `https://YOUR-ADDRESS/hooks/twilio/sms`. The app rejects any request that is not signed by Twilio.
8. **Test with real phones before any customer does.** Use two phones you own:
   - Call the number and answer (press a key). No text should arrive.
   - Call and let it ring out. A text should arrive within a minute.
   - Call and let it go to the owner's voicemail. A text should arrive. (If it doesn't, tell me: the voicemail check depends on Twilio behavior I could not test from here.)
   - Reply with a problem, pick a time, then reply `STOP`. Confirm nothing else arrives.
   - Check the Board and the calendar link.
9. **Start with one friendly shop**, keep `MAX_FIRST_TEXTS_PER_BUSINESS_PER_DAY` low at first, and read every conversation for the first week.

## What it costs to run (from Twilio's price page, October 2026)

Texts cost $0.0083 per segment plus a carrier fee of roughly $0.0035 to $0.005 per segment, sent or received. A typical booked job in testing used about 8 outgoing segments plus 2 incoming, which is roughly **10 to 15 cents of texting per booked job**. Add the number rental (about $1.15 a month) and the minutes for forwarded calls (see Twilio's voice price page). Hosting is separate: use the host's cheapest paid plan that includes a persistent disk. Twilio does not publish its registration fees on that page, so check them before you commit.

A message over 160 plain characters becomes two segments, and curly quotes or emoji shrink that limit to 70. The app writes its own texts in plain characters and cleans customer quotes in alerts to match.

## Risk check results

I checked the app against five common ways a quickly built app costs money or creates liability.

| Check | Result |
|---|---|
| Hosting bills | **Low.** The pages are tiny: about 27 KB of CSS and script, plus two bundled fonts (about 70 KB together, downloaded once and then cached). No images or video, and nothing is loaded from another site. |
| Accessibility | **Passed my checks, not a full audit.** Every field has a label, every icon is decoration with a text label beside it, and the whole app works from the keyboard, including picking a time. I measured every text and control color pair in both themes (all pass 4.5:1 for text and 3:1 for borders and focus rings), checked that a focused control is never hidden behind the phone tab bar, that nothing scrolls sideways at 360, 375 and 390 pixels wide, and that tap targets are at least 44 pixels. Animation turns off for people who ask their device for less motion. I did not test with a screen reader. |
| Open database | **Passed.** There is no browser-to-database connection. Every query uses placeholders (three string-built queries use fixed names from the code, never user input). A test proves one business can't read or change another's customers, and booking links are 22 random characters. |
| Messaging consent | **Needs a lawyer's review before launch.** The first text identifies the sender and opt-out works. The review-request text is the most legally gray, so the booking page now tells customers they agreed to texts including a review request. This is not legal advice. |
| Runaway costs | **Fixed.** I found that open sign-up with live texting would let a stranger aim calls and texts at your account. Sign-up is now off by default when texting is live, owners can't change the texting number, owner cell phones must be US or Canadian, and texts to other countries are blocked. Sending is capped per number, per business and per day, with a master off switch. |

## How it looks

The design follows the "home services" match in a UI design database (trust blue with safety orange) and was checked against its pre-delivery list.

- **Colors:** navy ink, blue for actions, safety orange for "needs you", red only for emergencies. Light and dark themes: it follows the device by default, and the moon/sun button in the top bar switches and remembers your choice.
- **Fonts:** Bricolage Grotesque for headings and numbers, Atkinson Hyperlegible Next for everything else (made for easy reading on small screens). Both are bundled in `jobping/static/fonts` so nothing is fetched from another site.
- **Phones:** the Board, Setup and Simulator links move to a tab bar at the bottom of the screen where a thumb reaches them. A customer's page opens with the **Call** button and the job, and the text history follows.
- **Credits:** the fonts are under the SIL Open Font License (license files sit next to them). The icons are Phosphor Icons, MIT license (`LICENSE-Phosphor-Icons.txt`).

## Not built yet

- Payments, quotes and invoices. By design: the assistant never quotes a price.
- Delivery receipts from Twilio (failed sends show only as failed).
- More than one worker, more than one time window per day, or several staff calendars.
- Languages other than English.
- A real-phone test. Two things depend on Twilio behavior I could not test here: the voicemail case above, and the signature check against a real signed request (it is tested against the documented method).

## Where things are

| File | What it does |
|---|---|
| `jobping/flow.py` | The conversation: what the assistant says and when. |
| `jobping/texting.py` | Sending texts, the safety limits, the Twilio connection. |
| `jobping/slots.py` | Which times are open. |
| `jobping/hooks.py` | What Twilio calls: call comes in, call ends, text comes in. |
| `jobping/owner.py`, `public.py`, `sim.py` | The Board and Setup, the customer booking page, the simulator. |
| `jobping/templates`, `jobping/static` | Pages, styles, the two fonts and the small scripts. |
| `jobping/icons.py` | The icons, drawn inline so no icon file is fetched. |
| `tests/` | 153 automated tests. |
