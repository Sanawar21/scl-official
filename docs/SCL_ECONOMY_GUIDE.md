# The SCL Economy — Season 2 Guide

**Section-C Cricket League · Official Money & Wallet Mechanics**

Season 2 runs on a **single wallet system**: every player has one bank account, and for
managers **that account IS the team's money**. There is no separate team purse — the
team starts with its manager's wallet and everything flows through it.

---

## 1. Your wallet

| Money | What it is | Used for |
|---|---|---|
| **Liquid cash** | Spendable balance | Auction bids, wager stakes, vault locks, trades |
| **Locked capital** | In the vault, earning 7%/match | Long-term savings (see the Vault Guide) |

- **Every player gets one wallet** — even if you never sign up, the league creates it for
  you (it runs on auto mode by default).
- **Only the admin can add balance** to a wallet (grants with a comment, e.g. "credit
  saved"). There is no player-facing deposit — don't ask, just request the admin.

---

## 2. Season 2 funding — the universal 10k

- **Every player is funded 10,000** before the S2 auction.
- **There is no tier purse.** Platinum/Gold/Silver teams all start from their manager's
  10k — the auction is a test of judgment, not of starting pockets.
- This is done **once**, before the auction, by the admin (**Fund all players**).

### 2.1 Pre-auction balancing grants
- The admins may **gift weak managers extra funds before the auction** to balance the
  field — e.g. a first-time or struggling manager may receive a top-up on top of the 10k.
- These grants are at the **admin's discretion**, applied per team, and are recorded with
  a comment (visible in the ledger). They happen **before** the auction so the extra
  money is available for bidding.
- There is no fixed formula — the committee decides case by case.

---

## 3. Where money comes from

| Source | Amount | Notes |
|---|---|---|
| **Universal funding** | 10,000 | Once, before the auction |
| **Match credit** | **250 to every player** | Per finalized match — the economy's heartbeat; auto-vaulted if auto mode is on |
| **Vault yield** | 7% per match | Compounding unless manual harvest (Vault Guide) |
| **Wager payouts** | variable | Winners get ≥ fair odds (Wagers Guide) |
| **Admin grants** | variable | Bonuses, "credit saved", compensation — admin only, with a comment |
| **Credit refund** | 1,000 / unused credit | Unspent draft credits refunded at draft end |

## 4. Where money goes

| Outflow | Amount | Notes |
|---|---|---|
| **Auction bids** | your bid | Charged at lot close when you win a player |
| **Squad-cost levy** | average squad cost | Charged to wallets that didn't spend in the auction — auto-applied when the draft completes (see below) |
| **Wager stakes** | your stake | Into the Yes/No pool; refunded or paid at resolution |
| **Vault locks** | your choice | Liquid → locked capital |
| **Fines** | see table | Field invasions, missed umpiring, etc. |
| **Player release** | 50% of auction price | Substitution Release Clause |
| **Sponsored announcements** | 200 per post | Match promotion / trash talk (Rule Book §6) |

---

## 5. The auction & credits

- Bidding is tier-by-tier (Platinum → Gold → Trade break → Silver → Phase B), in **50-unit
  increments** from the base price (3,000 / 2,000 / 1,000).
- Teams draft with **credits**: 8 total, 3/2/1 per Platinum/Gold/Silver player won.
  Unused credits refund **1,000 each**.
- A full roster is the **manager + 3 bought players** (4 total). If players remain unsold
  after the draft, admins assign them to incomplete teams.

### 5.1 Squad-cost levy (read this — it affects everyone)
When the draft completes, the **average squad cost** (total auction spend ÷ number of
teams) is **deducted from every wallet that didn't spend in the auction**. It comes out
of liquid cash first, then the vault for auto-mode accounts. This keeps the league fair:
teams that sat out the auction still contribute their share of the market value.

---

## 6. Auto mode — the hands-free option

- **Auto mode ON**: everything that comes in (grants, the 10k, the 250/match credits)
  goes **straight into your vault** and compounds at 7% per match. You never manage cash.
- **Auto mode OFF**: money lands in liquid cash; you manage bids, stakes, and locks
  yourself.
- Flip it anytime from `/account`. **Needed off** if you want to bid or stake.
- Wallets created for players who never signed up default to **ON**.

---

## 7. Fines & penalties (Season 2)

| Violation | Fine |
|---|---|
| **Missed umpiring quota** — fewer than 3 matches volunteered by season end | **1,500** |
| **Field invasion** by your team's player/member | **500** per incident |
| **Recurring disputes / misconduct** | Manager authority stripped (Kela Protocol) — team handed to players |
| **Match-fixing / collusion** | Market voided, stakes refunded, disciplinary action |

---

## 8. Money model at a glance

```
Admin grants ─┐
10k funding ──┤
250/match ────┼──► WALLET (liquid) ──► vault lock ──► VAULT (7%/match, iron lock)
Wager payouts ─┘          │
                          ├──► auction bids (players)
                          ├──► wager stakes (Yes/No pools)
                          ├──► fines / releases / sponsored posts
                          └──► squad-cost levy (draft end, non-spenders)
```

---

## 9. Key numbers to remember

| Thing | Number |
|---|---|
| Universal funding | 10,000 |
| Match credit (every player) | 250 |
| Vault yield | 7% per match (cap 12 matches) |
| Platinum / Gold / Silver base | 3,000 / 2,000 / 1,000 |
| Bid increment | 50 |
| Draft credits (total) | 8 (3 / 2 / 1 per tier) |
| Credit refund | 1,000 each |
| Squad-cost levy | average auction spend, non-spenders |
| Umpiring quota | 3 matches (else 1,500 fine) |
| Field invasion | 500 |
| Sponsored announcement | 200 |
| Player release | 50% of auction price |

---

_Section-C Cricket League · SCL Season 2 · Official Documentation_
