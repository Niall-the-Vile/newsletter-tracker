# Newsletter Tracker

Keeps track of who gets which newsletter, how many copies, and how it is sent (by mail, by email, or both).
It makes the mailing sheet for each issue and remembers every past mailing.

This folder holds the program only. No names, addresses or email addresses belong in it. Never put the
data file, a backup or a mailing sheet in a code repository.

## How to run it

1. Python 3 and one add-on are needed once per PC: `pip install -r requirements.txt` (this adds `openpyxl`,
   which reads Excel files).
2. Double-click **run.bat**. (Or run `python main.py`.)
3. The first time, choose the shared data file on OneDrive. Everyone points at the same file.
   Each person keeps their own copy of this program folder. To set up a new PC, run **fresh_copy.bat**.

The window title shows the program version and which data file you are using.

## The three tabs

**People & Churches** is one list of everybody. Use the Find box at the top (it works from any tab), or filter by
Type, Fund and State. Tick **Needs attention** to see people with no fund or no full address, and churches that
still need someone picked to receive church mail. Click a row to see the details on the right:

- **What they get**: each newsletter, how many copies, how it is sent, and when it was last mailed.
  Use Add, Change copies and Stop sending.
- **Reps** (for a church): the star marks the rep who receives the church's mail. Use *Receives church mail* to
  move the star, Add rep, Remove rep, or Move a rep to another church.
- **History**: past mailings and changes for that person or church. It loads when you open the tab.

Select several people and use **Set fund...** to give them all a fund at once. **Remove** takes people off the
list but keeps them on file. Tick **Show removed** and press **Restore** to bring them back. Removing more than
10 people at once asks you to type the number.

**Mailings** lists every past mailing. **Create mailing...** makes a new one (see below). **Open** shows who was
in a past mailing.

**Setup** has Newsletters, Funds, Old subscriptions, Church grouping, Import, Bounces and Activity.

## Making a mailing

1. On the Mailings tab press **Create mailing...**
2. Pick the newsletter and type an **issue label** such as `Fall 2026`. Choose *Mailing sheet (by post)* or
   *Email list*. Nothing is ever sent by this program. The email list is only a list of addresses.
3. Read the summary. Anything under **Fix before saving** must be fixed first (for example no address, or a fund
   that does not match the newsletter). **Worth a look** items are warnings. **Show skipped** lists who was left
   out and why. The summary also says who was added or removed since the last issue.
4. Press **Save the sheet...** and choose where to put the file. The mailing is only recorded once the file has
   saved. If saving fails, nothing is recorded.

A church that gets 40 copies is one row addressed to the rep who receives church mail. That rep does not also get a
personal row for the same newsletter. Someone who gets a newsletter by **Both** is in the mailing sheet and in the
email list.

If you use an issue label that was already made, the program asks before making another.

## Issue labels

The label is the name of the issue, such as `Fall 2026` or `Spring 2027`. It goes on the mailing sheet, in the
list of past mailings, and in each person's History. Use the same style every time so the list sorts well.

## Past mailings

Open a past mailing to see exactly who was in it, with their address and copies as they were that day. Later
changes to a person, church or address never change a past mailing. Press **Save the file again...** to get the
same file back, byte for byte. **Save packing sheet...** gives a sheet by state and church with the issue total.
Past mailings are kept forever. A newsletter that has been mailed cannot be deleted; it is archived instead.

## Backups and restore

- A backup is made automatically the first time the program opens each day, and before every import, bulk change
  or mailing. The last 30 are kept.
- Each backup is saved twice: in a `backups` folder next to the data file, and on your own PC in
  `%LOCALAPPDATA%\NewsletterTracker\backups`.
- **File > Back up now** (or the button at the top) makes one on demand.
- **File > Restore from backup...** lists the backups with how many people they hold. It makes a fresh backup
  of today's data first, then goes back to the one you choose. Everyone else must close the program first.

## Two people at once

Only one person edits at a time. If someone else already has the program open, yours opens **view only**, with a
note such as "Maria is editing since 10:42". If their program has been quiet for two minutes, a **Take over
editing** button appears. Use it only if you are sure they have closed it.

If OneDrive makes an extra copy of the data file (a name like `newsletter_tracker-PC1.db`), the program warns you.
Ask Niall before using either one.

## What an update does to the shared file

Programs from before version 2 stored people and subscriptions only. The first person to open the new program on
the shared file sees a one-time update message.

- Everyone else must close the program first.
- Two verified backups are made before anything changes, and the update stops if the counts of people,
  newsletters and subscriptions do not match afterwards.
- The update only adds new tables. Your existing people, newsletters and subscriptions are not renamed, moved or
  deleted.
- After it, an **old copy of the program can look but not change anything**. It shows "This copy of Newsletter
  Tracker is out of date. Please update your copy from the shared folder." Update the program folder (use
  fresh_copy.bat) and it works again.
- A newer data file will not open in an older program. Update the program.
- The old MAAP subscriptions (one copy each) are not changed by the update. They wait in **Setup > Old
  subscriptions**, where you choose what they become.

## Other screens in Setup

- **Newsletters**: add, rename, limit a newsletter to a fund, archive or delete.
- **Funds**: each person or church belongs to one fund. A newsletter with a fund goes only to that fund.
- **Old subscriptions**: check the old one-copy MAAP subscriptions and move them in one step. You can undo.
- **Church grouping**: checks church names (name plus conference) and shows what would be created. Nothing is
  saved until you press Create. Similar names can be merged by hand. An earlier grouping can be undone.
- **Import**: choose a CSV or Excel list to see what would change before anything is saved. Blank cells never erase
  what is already on file. Subscribing everyone is off unless you tick it and choose a newsletter.
- **Bounces**: mark an email address as bounced, or paste in a list. A bounced address is left out of email lists
  until the address is corrected. A full or away mailbox is left out after 3 in a row.
- **Activity**: a short log of who changed what.

## Help menu

- **Get started** shows the setup steps and ticks them off as they are done. It opens by itself the first time.
- **Copy diagnostics** copies the version, data file location, data file version, who is editing and the last
  backup. Paste it into an email if something looks wrong. It holds no names or addresses from the list.

When something looks wrong with the data, a blue bar under the menu says what, with buttons that open the list
to fix. Press Hide to dismiss it until the next time you open the program.

## For developers

See TESTING.md.
