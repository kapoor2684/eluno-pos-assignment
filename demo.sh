#!/usr/bin/env bash
# One command: fresh flaky mock (429/503 ON) + fresh till DB, then the whole Must flow. Usage: ./demo.sh
set -u; cd "$(dirname "$0")"; export ELUNO_DB=.pos/demo.db; rm -rf .pos; ./run_mock.sh
TODAY=$(python3 -c "from eluno_pos.store import now; print(now().date())")
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
step "1. customer + latest valid prescription (priority/campaign flags shown)"; ./pos lookup --phone +919000000005
step "2. strong prescription: wrong lens is BLOCKED with an actionable reason"; ./pos quote --store MM --staff S101 --phone +919000000011 --pair FR-0005:LN-0006
step "3. expired prescription is blocked"; ./pos quote --store MM --staff S101 --phone +919000000000 --pair FR-0005:LN-0006
step "4. bill: progressive + frame, offers (best outcome), GST per line, charge, invoice no, Shopify order"; ./pos bill --store HSR --staff S105 --phone +919000000005 --pair FR-0003:LN-0043 --pay upi --channel whatsapp_booked
step "5. printable invoice"; ./pos invoice --invoice ELU/HSR/FY26/00003
step "6. cash till: open, cash sale (external Rx needs optometrist confirmation), paid-out"; ./pos till open --store JN --staff S120 --float 3000
./pos bill --store JN --staff S117 --phone +919000000003 --pair FR-0010:LN-0001 --pay cash --confirm-rx S119 | tail -2; ./pos till out --store JN --staff S120 --amount 120 --reason courier
step "7. full refund (frame restocked at HSR, lens not, credit note, Shopify refund)"; ./pos refund --invoice ELU/HSR/FY26/00003 --reason CHANGED_MIND --staff S105
step "8. day-end from Shopify orders"; ./pos dayend --store HSR --date "$TODAY"; ./pos dayend --store JN --date "$TODAY"
step "9. close till (counted ₹80 short of expected)"; EXP=$(python3 -c "print(3000+8647.20-120-80)"); ./pos till close --store JN --staff S120 --counted "$EXP"
