# The outreach pipeline

Find prospects, mail them from your Gmail, track replies, invoice, collect.
One command runs the loop; the database is the memory, so it can be killed and
restarted at any point.

```
RESEARCHED -> DRAFTED -> SENT -> REPLIED -> QUOTED -> WON
                              \-> BOUNCED  \-> CLOSED  \-> OPTED_OUT
```

A prospect moves forward one step at a time and `advance()` is the only thing
that moves it. Illegal transitions raise rather than silently correcting, which
is what makes it safe to run unattended.

## Commands

```bash
earner pipeline import --file leads.csv   # company,email,evidence,contact_name,website
earner pipeline draft                     # write a specific email per prospect
earner pipeline preview                   # read them before anything is sent
earner pipeline send                      # DRY RUN by default — opens no socket
earner pipeline send --live               # real mail (needs the confirmation below)
earner pipeline replies --live            # scan the inbox, honour opt-outs
earner pipeline quote --ref p-abc --amount 1800
earner pipeline collect                   # confirm payments, record revenue
earner pipeline run --live                # the whole loop
earner pipeline status                    # where everything stands
```

## Before it can send

```bash
earner connect                            # sets GMAIL_USER + GMAIL_APP_PASSWORD
export OUTREACH_SEND_CONFIRMATION=I_UNDERSTAND_THIS_EMAILS_REAL_PEOPLE
```

Two independent gates: `--live` opens the socket, the environment variable
permits it. A script that calls the sending function directly still cannot mail
a stranger unless a human set that variable in that shell.

## Why the volume controls exist

Not caution — survival. Google's abuse systems watch for a burst of
near-identical mail to strangers with no prior relationship to the sender. A
personal account that does that gets suspended, and the suspension takes the
calendar, the drive, and every service behind "Sign in with Google" with it.
The business ends on day one, and not because the pitch was wrong.

So the pipeline enforces, in code rather than by convention:

| Control | Value | Why |
|---|---|---|
| Daily cap | 20 | Well under Gmail's limit, and more than a solo operator can follow up on anyway |
| Send spacing | 25–90s, randomised | A message every 1.000s is a signature no human produces |
| Duplicate sends | impossible | Unique index on the address, not a remembered set |
| Follow-ups | 2, then closed forever | |
| Opt-out | permanent, no removal path | Checked before anything else in a reply |

If you want more volume than this, the answer is a separate sending domain with
a warmed inbox — not a higher number here. Raising the cap on a personal Gmail
buys a few extra sends and costs the account.

## Why evidence is mandatory

`Prospect.validate()` refuses anything with under 25 characters of evidence.
That field is the sentence that makes the email specific to the recipient, and
it is pasted into the body verbatim. Without it the only thing left to send is a
template — which performs worse than sending nothing and burns the address list
on the way.

## Why a reply is required before an invoice

`quote()` accepts only a prospect at `REPLIED`. Invoicing someone who never
answered is the worst thing this program could do to the sender's reputation,
so the state machine makes it impossible rather than merely discouraged.

## Why revenue only moves on provider confirmation

`collect()` asks the payment provider, never the customer's email. A client
writing "paid it this morning" does not move the ledger. `Ledger.settle()` is
the only path by which revenue enters the system, and it deduplicates on the
provider's event id, so running `collect` twice cannot double-count.

## What this does not do

**It does not find the prospects.** The CSV has to come from somewhere, and
inventing company email addresses would mean mailing strangers who were never
researched. Research is a separate, deliberate step.

**It does not take the discovery call.** The pipeline fills the top of the
funnel and handles the paperwork at the bottom. The 15 minutes in the middle is
yours.
