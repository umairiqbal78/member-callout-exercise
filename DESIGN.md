# Callout System Design

## Problem Statement

**DENISE OKAFOR (Client message):**
> "Hey, we keep missing people on the callouts. Half of Local 27 didn't know about Thursday's emergency meeting until it was too late and it's caused real problems with the contractor situation. Members are saying they never got anything, or they got it but by the time they saw it the meeting was already over. Also, I need to know before I leave the hall on the day of a callout who's actually coming so I'm not standing in an empty room. Oh and can members outside our local see the wireman apprentices' contact info too? someone mentioned that during onboarding and I want to make sure that's not happening. We need this sorted before the next round of contract talks, whenever that ends up being scheduled."

---

## 01 The Ask

### Requirements
**Restate what you're actually going to build, in your own words, as if explaining it back to Denise.**

Hi Denise, sorry about Thursday. Here’s what we’ll build. When you send a callout, every member of your local gets it in the app, and it waits there until their phone has signal, so a day underground doesn’t mean missing it. The phone notification is just a nudge on top. You’ll see how many members have received it, opened it, and replied *Coming* or *Can’t make it*. The numbers refresh every few minutes, and you can refresh them yourself right before you leave the hall. Resending a callout will never give anyone a second copy. And nobody outside your local can see your members’ contact info, apprentices included. We’ll confirm that isn’t happening today either.

### Assumptions
**List your assumptions for everything the message doesn't specify:**
* **Scope:** Leaders send callouts and see results for their own local only. Each member belongs to one local for now.
* **Idempotency:** A callout goes to every member of that local exactly once. A retry or a restart never creates a second copy.
* **Delivery:** “Delivered” means the member’s app actually has the callout, not just that we pushed it.
* **Responses:** Replying is requested, not required. The dashboard shows *Coming*, *Can’t make it*, and *No response*, and the three add up to the total.
* **Dashboard:** The dashboard shows counts only, refreshed on a timer or by the leader.
* **Completion:** A callout is done at the meeting time. The numbers freeze, and anything that arrives later is marked missed.

### Flags
**Flag anything in the message that concerns you:**
* **Discrepant Numbers:** The numbers don’t add up. Push fails silently 5–10% of the time for about 1,100–2,200 people in our largest local. But half that local is 11,200, so other factors may be involved: members offline, a late callout, or people missing from the list. Delivery records should clarify. 
* **Privacy Leak:** I’d also first check the report that apprentice contact info is visible to other locals. 
* **Timeline:** Contract talks have no date, so I assume they’re soon.

### Questions for Client
1. **When was Thursday’s callout sent, and when was the meeting?** 
   * *Assumption:* It went out too close to the meeting for offline members, so the app will show how much notice each callout gave.
2. **Did anyone actually see apprentice contact info, and should other members of your local see it, too?** 
   * *Assumption:* Only leaders in their own local can see it.
3. **When are the contract talks, and what has to be done by then?** 
   * *Assumption:* It’s soon, so reaching members and the headcount of who’s coming come first.

### The Numbers You're Designing For
* **Members total:** 187,000
* **Union locals on one platform:** 43
* **Largest local (one callout goes to all of them):** 22,400
* **Push notifications that silently fail roughly:** 5–10%
* **How long a member can be offline:** hours, sometimes days

### Core Rules
* **Rule 1:** Nobody sees or touches data outside their own access.
* **Rule 2:** A retry or a restart must not deliver twice to the same member.

---

## 02 Part A: The Design

### 1. Data Model

```sql
locals                     -- (unchanged)

members
  id, local_id, user_id    -- (1:1 with the login account)
  full_name, email, classification, status
  index (local_id, status)

leadership_roles
  member_id, local_id, role, granted_by, granted_at, revoked_at
  unique (member_id, local_id)

member_devices
  member_id, push_token, platform, last_seen_at, disabled_at

announcements
  id, local_id, created_by, title, body, needs_ack
  starts_at                -- meeting time, null for plain announcements
  status                   -- draft | sending | sent | closed
  recipient_count, final_counts (jsonb), sent_at, created_at
  idempotency_key          -- unique per (local_id, key)

announcement_recipients    -- one row per announcement per member
  id (bigint), announcement_id, member_id, local_id
  delivered_at, read_at, acknowledged_at
  response                 -- null | coming | cant_make_it
  responded_at, push_sent_at, push_error
  unique (announcement_id, member_id)
  index (member_id, id desc)
  fillfactor 80
```

* **Who it went to:** When a send starts, I create one recipient row for every active member of that local. A missing row means the member was never targeted, and a row with every field empty means “nothing happened”. I rejected creating rows lazily when members open the app, because then “nothing” can’t be counted and a restarted push worker has no record of who was already pushed.
* **What each member did:** Each action has its own timestamp, empty until it happens and set only once (`COALESCE`), so a late or repeated event from an offline phone can’t undo anything. Delivered means the app confirmed it stored the callout. A reply also sets read and acknowledged. I rejected a single status column, because read and coming aren’t one ladder and an offline phone can report events out of order. I also rejected an event log alone, because every dashboard count would have to replay events.
* **Counts:** The dashboard runs one grouped count with `FILTER` over a single callout’s rows, about 22,400 at most, and caches the result for around 10 seconds so four viewers cost one query. I rejected counters on the announcement row, because thousands of members updating one row would queue behind each other.
* **Leadership:** Leaders are granted a row in `leadership_roles` tied to one local. I rejected a role column on `members`, because any member-facing serializer that exposes it is one mistake from letting someone promote themselves. With a separate table, no member endpoint touches it.
* **Changes to the given tables:** `announcements` gets `starts_at` to decide what counts as missed, `status` because `sent_at` can’t tell “still sending” from “crashed halfway”, `created_by` for audit, and `idempotency_key` so a retried send finds the same announcement. Recipients carry `local_id`, with foreign keys to both parents that include it, so the database refuses a recipient from another local. `push_sent_at` lets a restarted worker skip members already pushed. Tokens live in `member_devices` so dead ones can be disabled.
* **Scale:** 187,000 members × 100 callouts a year is about 18.7 million recipient rows, only a few GB, so size isn’t the risk. The risks are update churn, cold data and bursts. Updated columns are left unindexed and the table has a lower `fillfactor`, so updates stay cheap. When a callout passes its meeting time, a scheduled job saves its final counts into `final_counts` and sets the status to closed. Recipient rows are deleted after 12 months (an assumption to confirm with the client), keeping the table near 19 million rows. Fan-out inserts run in chunks in the background, and receipts arrive in batches.

### 2. The Send Path

#### Inside the First Request:
1. The leader presses Send. The request carries an idempotency key. The server loads the announcement scoped to the leader’s own local and checks that they hold a leadership role there.
2. One compare-and-set: `UPDATE announcements SET status='sending', sent_at=now() WHERE id=? AND status='draft'`. If it changes nothing, this is a retry, so the server returns the current state instead of starting a second send.
3. After the commit, one fan-out job goes on the queue and the server returns `202` with status “sending”. No member is touched during the request. The leader waits for one indexed update and one enqueue, not for 22,400 phones.

#### Somewhere Else (Queue Workers):
* **Fan-out:** A worker reads the local’s active members and inserts recipient rows in chunks of about 2,000, with `ON CONFLICT DO NOTHING`, then sets `recipient_count`. From this point the callout is in every member’s inbox, and the inbox is what counts.
* **Push:** Each job covers 500 recipients, the size of one FCM batch. It first claims its rows with `UPDATE ... SET push_sent_at=now() WHERE push_sent_at IS NULL RETURNING member_id`, then sends one batch for only the rows it claimed and records failures in `push_error`. Claiming before sending means a crash can skip a nudge but can never repeat one. I accept that because push is only a nudge. I rejected send-then-mark, because a restart would buzz people twice.
* **Completion:** When no rows are left unclaimed, the status becomes `sent`.
* **On the phone:** A push wakes the app. A member who was offline for hours or days gets the callout when the app next syncs its inbox, which doesn’t depend on any push. The app stores it locally and confirms receipt in one batch call, which sets `delivered_at`.

Workers acknowledge a task only after finishing it (`acks_late`), so a crashed task is rerun. Every step above is safe to run twice. A periodic sweeper re-queues any announcement stuck in sending, which covers a crash between the commit and the enqueue.

#### The Status Screen
The stats endpoint reads from a Redis cache with a 10-second lifetime. On a miss, one request runs the grouped count while the others serve the old value, so there’s no stampede. The app refreshes every 15 seconds or when the leader taps refresh. Four viewers cost about one query per 10 seconds, instead of four per second. After the callout closes, the endpoint returns `final_counts` and never reads the recipient table. I rejected live streaming and a count per viewer, because both add load and complexity for no benefit.

### 3. The Two Rules, by Design

**Rule 1: Nobody sees or does more than they should.**
* **Between locals:** The server finds your local from your login, never from the request. Every query is filtered to that local, so another local’s id just returns “not found”. The database adds a second lock: a recipient row can’t be linked to a member from a different local.
* **Member versus leader:** Leaders are rows in a separate `leadership_roles` table, checked on every request. Members can only read and update their own row, and no member endpoint can touch roles.
* **A forgotten endpoint next year:** New endpoints are closed by default, and they inherit the local filter from one shared base class. A test in CI fails the build if any endpoint has no explicit permission, and tries every endpoint as a user from another local, expecting “not found”.

**Rule 2: Nobody gets it twice.** 
Each protection is in the database:
* **Retried Send:** Only the first press changes the status from draft to sending. A retry changes nothing.
* **Restart while creating rows:** One row per member per callout is enforced by a unique constraint, so a rerun can’t add a second.
* **Restart while pushing:** A worker marks a member as pushed before sending, so a rerun skips them. In the rare crash, someone misses one nudge but still has the callout in the app.

### Precautions for Production
* **Rule 2 Alert:** Alert if pushes sent ever exceed the number of recipients.
* **Rule 1 Alert:** A test account in one local tries to read another local’s callout every few minutes and alerts on any success, and a nightly check looks for rows where the locals don’t match.