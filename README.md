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
- **Newsletters tab** — pick a newsletter on the left to see its recipient list, quantities, and
  the total copies needed. Use "Add Recipients..." to subscribe people from your master list,
  double-click a row to edit its quantity, and "Export Mailing List (CSV)" to get a
  print/mail-merge-ready file (name, address, church, quantity).
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
