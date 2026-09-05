# Wager & Risk Management — SCL Season 3 Guide

**Section-C Cricket League · Operational Protocol**

SCL's prediction market is a **pooled Yes/No market**. It is not a bookmaker: stakes are
pooled, odds are set by consensus probability, and the **House (league administration)
guarantees fair payouts** so a thin market never cheats the winner.

---

## 1. How the market works

- Every market asks a **Yes/No question** — e.g. *"Will Thunder win Match 5?"*
- Everyone stakes into one of **two pools**: **Yes** or **No**.
- Stakes are **not matched 1-to-1**. All the money pools together, and at resolution the
  **winning side splits the pot**.
- The league administration (the **House**) acts as the **liquidity provider of last
  resort** — see the House Guarantee below.

---

## 2. The lifecycle of a wager

```
Propose → Calibration → Financial veto → Peer phase → Resolution
                                                        ↘ voided
```

### 2.1 Propose
A player (or manager) proposes a condition and places the **first stake** — this opens
the market. The proposer's stake starts the pool.

### 2.2 Calibration
Admins assign an **objective probability** to the condition (e.g. "Thunder has a 40%
chance"). Multiple admins give **blind estimates**; if they disagree, the **mathematical
average** is used. This locks the **fair odds**:

- fair odds for a side = 100 ÷ probability (as a percentage).
- E.g. a 40% side pays **2.5x**; a 25% underdog pays **4x**.

### 2.3 Financial veto
A mandatory solvency check — if the market would **risk a club's long-term
participation** (bankruptcy, inability to enter the next draft), the market is **vetoed
and every stake refunded 100%**.

### 2.4 Peer phase (betting open)
Other players enter the **Yes** or **No** pools. The market is now **vetted** and open
for staking. You can stake any amount you have in liquid cash; a live preview shows
exactly what you'd win.

> **The House guarantee (automatic).** If peer interest is too thin to pay the
> risk-adjusted payout (e.g. the 4x underdog wins but the pot is small), the **House
> automatically tops up** so the winner gets their full fair-odds payout. You will see
> this live on every market: *"House covers: Yes win → N · No win → M"* — it updates the
> moment anyone stakes, on either side.

### 2.5 Resolution
The admin resolves with the **winning side**:

- Winners receive **at least stake × fair odds** (the House covers any shortfall), and
  if the pot is fat they split **everything** pro-rata (which can pay more than fair odds).
- Losers' stakes fund the pot.
- Payouts land in your **liquid cash** automatically.

### 2.6 Void
Ambiguous, impossible, or integrity-compromised markets are **voided — 100% refunds** to
everyone.

---

## 3. Financial solvency (bankruptcy veto)

- **The rule:** admins reserve absolute authority to **cap or cancel** any wager that
  threatens a club's long-term participation.
- **The trigger:** if a stake risks immediate bankruptcy, or would leave a club unable to
  participate in the upcoming draft, intervention is **mandatory**.
- When it happens: the market is vetoed and **all stakes are refunded**.

---

## 4. Integrity & anti-fraud

- **Match-fixing & collusion** are monitored continuously — suspicious gameplay or
  unnatural betting volumes are flagged.
- If integrity is compromised: the market is **voided immediately** and **all stakes are
  refunded**.
- Calibration is blind and averaged specifically so no single admin can game the odds.

---

## 5. Operational edge cases

| Situation | What happens |
|---|---|
| **Ambiguous / impossible condition** | Market voided — **100% refunds** |
| **Mid-wager news breaks** (injury, weather, lineup) | Pools are **frozen**; a Phase 2 market may open with updated odds |
| **Admins disagree on risk** | Mathematical average of blind estimates is used |
| **Nobody bets the winning side** | Market is voided; House retains pot |
| **House can't cover the guarantee** | Resolution is blocked until House funds are topped up by admin |

---

## 6. Playing on the platform

- **Board** (`/wagers`) — every market as a card: pools, pot, fair odds, and the live
  **House guarantee chip**.
- **Detail** (`/wagers/<id>`) — full pool visual, the House guarantee banner, the stake
  form with the live **"you'd win X"** preview, and every bet + lifecycle event.
- **Propose** — from the board, open a market with your first stake.
- **Stake** — only on **vetted** markets; uses your **liquid cash** (auto mode holds money
  in the vault, so switch it off to stake).

**Status chips:** `proposed` → `calibrating` → `vetted` (open) → `frozen` → `resolved` /
`voided`.

---

_Section-C Cricket League · SCL Season 3 · Official Regulatory Framework · Updated August 2026_
