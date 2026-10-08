# Newsletter Tracker

Tracks who needs which newsletter, and how many copies, across:

- **Recipients** — a master directory of people (reps and members), with church, conference,
  title, address, phone, and email.
- **Newsletters** — the publications you send (starts with *MAAP Newsletter* and *Our Health*;
  add more anytime from the Newsletters tab).
- **Subscriptions** — which recipients get which newsletter, and the quantity each one needs
  (e.g. a church rep who needs 15 copies of the MAAP Newsletter to put in mailboxes, vs. an
  "Our Health" member who gets 1 copy mailed straight to them).

## Running it

Requires Python 3 (already installed on this machine) and the `openpyxl` package for reading
`.xlsx` files:

```bash
pip install -r requirements.txt
```

Then either double-click **run.bat**, or run:

```bash
python main.py
```

### First run

You'll be asked where the data file should live. Choose the default (next to the program) for
a single-user setup, or point it at a location on the shared Anabaptist Brotherhood OneDrive so
everyone's copy of the app uses the same data. Each person needs their own copy of this program
folder, but they should all be pointed at the **same** `.db` file for the data to be shared.

> **Note on sharing one file over OneDrive:** this works well as long as two people aren't
> editing at the exact same moment. If that ever happens you may see a "database is locked"
> message — just wait a few seconds and try again. For a small team doing occasional updates,
> this is a non-issue in practice.

## Using it

- **Recipients tab** — add, edit, search, and filter your master list of people. Each person
  has a Type (Member / Rep / Other).
- **Mailings tab** � "Create mailing..." asks for the newsletter and an issue label, shows a summary
  with the problems to fix and who was left out (and why), then saves the CSV sheet. The mailing is
  only recorded once the file has saved. Open a past mailing to see its addresses, save the exact
  same file again, or get a packing sheet by state and church.
- **Setup > Newsletters** � add and rename newsletters, limit one to a fund, archive or delete.
  Each person's or church's copies and delivery are changed in the People & Churches detail pane.
- **Import tab** — bring in an existing CSV or Excel mailing list. It auto-detects the columns
  used in your existing files (Master/CLP-style "First Name/Last Name/Address 1/..." or the
  "Our Health" quarterly export style "Name/PrimaryAddressLine1/..."). Matching people (same
  name + address) get updated instead of duplicated, so re-importing a refreshed list is safe.
  You can optionally subscribe everyone in the imported file to a newsletter at the same time.

## Data model

```
people           one row per person (rep or member)
newsletters      one row per publication
subscriptions    person <-> newsletter, with a quantity (copies needed)
```

## Churches, reps and importing

- **People & Churches**: set Type to *Church* to list churches. Select one to see its reps. The star marks the main
  contact; use *Make default* to move it. Tick *Needs attention* to list churches with several reps and no main contact.
- **Setup > Church grouping**: checks church names (name plus conference, or state when there is no conference) and
  shows what would be created. Nothing is saved until you press Create. Similar names can be merged by hand, and an
  earlier run can be undone.
- **Setup > Import**: choosing a file shows what would change (new, updated, skipped and why, similar church names,
  ignored columns) before anything is saved. Blank cells never erase existing data, and subscribing everyone is off
  unless you tick it and pick a newsletter.
