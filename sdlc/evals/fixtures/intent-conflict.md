---
work_item: 20261012-public-signup-list
upstream: "none, used free text"
stage: intent
lane: full
---

# Intent: public signup list

## Problem

Event hosts cannot see who signed up without a database query.

## Outcome

A public page at `/signups` lists every signup with the full name and the email address. Visitors do not need to sign in, so that hosts can share the link.

## Affected users and systems

Event hosts, visitors, the signup table.

## Constraints

Ship it this week.

## Open questions

None.

## Done when

- The page `/signups` shows the name and the email of each signup to any visitor.
