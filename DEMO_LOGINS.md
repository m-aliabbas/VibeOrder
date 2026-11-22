# VibeOrder: demo staff logins

These are the demo accounts that `make setup` / `make seed` (`manage.py seed_demo`) create.
**Demo data only.** On a real install, a manager creates each person in the app (**Staff → Add person**)
and everyone chooses their own password.

## Where to sign in

| What | Address | Who |
|---|---|---|
| Staff app (tablet / counter PC) | Flutter app; web build at http://localhost:8080 | everyone |
| Kitchen display (KDS) | http://localhost:8000/kds/ | cooks, packers, managers |
| Owner portal | http://localhost:8000/owner/ | owner, managers |
| A4 ledgers | App → **Ledgers** → *Print A4* | managers, owner |

The app asks for the **server address** only the first time (default `http://localhost:8000`; on the
restaurant network use the counter PC's IP, e.g. `http://192.168.1.10:8000`).

## Branch B1: Demo Pizzeria, Gulberg

| Name | Username | Password | Role | Lands on | Can do |
|---|---|---|---|---|---|
| Zara | `zara` | `zara123` | Manager | Dashboard | Everything: dashboard, ledgers, staff accounts, printers, approvals, refunds, voids, stock, supplier payments |
| Hamza | `hamza` | `hamza123` | Cashier | Counter | Take orders, payments, discounts, drawer, phone/delivery orders, dispatch, reprints |
| Bilal | `bilal` | `bilal123` | Waiter | Floor | Open tables, send orders to the kitchen, amend, move tables, serve ready food |
| Sana | `sana` | `sana123` | Waiter | Floor | Same as Bilal |
| Usman | `usman` | `usman123` | Cook | KDS / KOTs | Kitchen queue, start / ready, acknowledge changes, reprint KOT |
| Faisal | `faisal` | `faisal123` | Packer | Dispatch | Packing checklist, hand over takeaway / delivery orders |
| Imran | `imran` | `imran123` | Rider | My deliveries | Own delivery jobs only |
| Kashif | `kashif` | `kashif123` | Rider | My deliveries | Own delivery jobs only |

## Branch B2: Demo Pizzeria, Johar Town

| Name | Username | Password | Role |
|---|---|---|---|
| Nadia | `nadia` | `nadia123` | Manager |
| Omer | `omer` | `omer123` | Waiter |

## Owner (both branches)

| Name | Username | Password | Role | Note |
|---|---|---|---|---|
| Ayesha | `ayesha` | `ayesha123` | Owner | Works at B1 and B2: the app asks which branch, unless the install sets `VIBEORDER_BRANCH=B1` |

## Good to know

- **Manager approval** (refund, void, discount over limit, drawer variance): the manager types *their own*
  username and password on the cashier's screen.
- **Forgot password:** a manager resets it in **Staff → tap the person → New password**. Anyone can change
  their own password from the account menu (avatar → *Change my password*).
- **Locked out:** 5 wrong passwords lock the account for 5 minutes (`VIBEORDER_LOGIN_MAX_ATTEMPTS`,
  `VIBEORDER_LOGIN_LOCKOUT`).
- **Someone leaves:** Staff → their account → switch off *Can sign in*. Their history stays.
- **Lost tablet:** Devices → *Block*. Everyone on it is signed out and it cannot sign in again.
- **Demo charts:** `make history` adds 30 days of past orders so the dashboard and ledgers have data.
