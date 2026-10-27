# Stakeholder Reply — Shopify POS Replacement (V1)

## 1. Questions to Resolve Before Production

Before finalising the production scope, several decisions need confirmation from the relevant stakeholders.

**Offers and discount policy — Retail and Finance:** Meena and Rohan have requested that festive, second-pair, and priority discounts stack, but the current offer configuration marks some offers as non-stackable. Should Retail change the offer configuration, or should the till continue to enforce the configured eligibility and stackability rules? I recommend honouring the configured rules until Retail and Finance approve a change. Manager discounts should require an authorised manager identity and reason code, with a defined 10% limit and an auditable record. The manual discount should replace automatic offers unless Finance explicitly approves a different policy.

**Tax and invoice compliance — Finance:** Vikram and the CA should confirm the applicable HSN tax rates and effective dates, how discounts are allocated across invoice lines, whether frame-and-lens bundles affect tax treatment, and whether e-invoicing requirements apply. Finance must also confirm the financial-year reset, invoice cancellation policy, and separate credit-note numbering requirements. Invoice numbers should be sequential per store, with cancelled invoices retained in the sequence and fully auditable.

**Prescription validation — Optometry:** Dr. Iyer should confirm the handling of expired prescriptions, missing ADD values, lens-range restrictions, and external prescriptions with axis values outside the 5-degree grid. Invalid prescriptions should produce actionable errors, and clinical values must never be silently rounded. Any exception should require an explicitly authorised optometrist.

**Refunds and remakes — Retail, Finance and Optometry:** The stakeholders must agree on the refund eligibility window, approval rules, and treatment of dispensed lenses. For remakes, we need defined cost-bearer categories, authorisation rules, and a reference to the original invoice. A remake must not rebill the frame or alter frame stock.

**Family billing — Retail and Finance:** Can one order support multiple prescription-linked lens lines for different family members under one payer? The buyer, invoice details, refund ownership, and prescription associations must be agreed before implementing family billing.

**Outages and reconciliation — Finance and Ops:** What is the escalation process when payment succeeds but Shopify is unavailable? I recommend retaining durable transaction state, retrying safely, and preventing duplicate charges or orders. Finance should define day-end reconciliation for gross sales, discounts, taxable value, tax, refunds, and payment methods, including how pending Shopify synchronisation is reported.

**Operational reporting — Founder and Retail:** Is the 10 am daily email required for the initial pilot? We need an owner for the recipient list, report delivery, and failure handling. The report should include yesterday's bill count, average bill value, and discounts by store.

## 2. Proposed V1 Scope

### Must Have

V1 should prioritise a reliable end-to-end billing flow while keeping Shopify as the system of record.

1. Customer lookup by phone and retrieval of the latest valid prescription.
2. Frame and lens cart with lens-matrix selection and prescription-range validation, including actionable errors for invalid combinations.
3. Data-driven offer evaluation using eligibility, effective dates, priority, and stackability rules.
4. Inclusive-GST calculations per line using HSN-specific rates, invoice dates, and paise-level rounding.
5. Payment-terminal integration followed by Shopify order creation, including line items, discounts, tax lines, prescription details on lens lines, sale transactions, and store-specific invoice numbers.
6. Full refunds recorded in Shopify, with tax reversal, a credit note referencing the original invoice, frame restocking at the original billing store, and no lens restocking.
7. Day-end reporting by store and date, including bill count, gross sales, taxable value, tax, discounts, refunds, and payment-method breakdown.
8. Automated tests for tax, discount and refund calculations, with retry handling and duplicate-order protection for transient failures.

The billing flow should target the requested sub-10-second experience, while ensuring that prescription checks, payment confirmation, and transaction recovery are never bypassed. Performance should be measured under realistic conditions.

### Should Have If the Must Flow Is Stable

Cash-drawer sessions, paid-ins and paid-outs, variance alerts above ₹200, sales-channel reporting, printable invoices, and the daily summary email should follow once the core billing and reconciliation flow is reliable.

### Deferred

Family billing, a complete remake workflow, partial refunds, split payments, zero-value orders, offer authoring using natural language, prescription-entry assistance, and anomaly detection should be deferred until their business rules and operational dependencies are agreed.

The beverage question in the queue tool and the Indiranagar footfall-camera issue should remain with their respective queue-tool and facilities owners, outside the POS replacement scope.

## 3. Risks, Contradictions and Out-of-Scope Requests

- **Offer stacking:** The request to stack every offer conflicts with explicit configuration rules. The system should follow the configured rules until Retail and Finance approve any changes.
- **Manual discounts:** An unrestricted manager discount conflicts with Finance's audit requirement. Manager identity, a reason code, and an approved discount limit are necessary.
- **Invoice numbering:** Gapless per-store numbering requires a single numbering authority per store. A local sequence can conflict if multiple till instances issue invoices independently. V1 should document its single-till-per-store assumption; a central allocator is needed before scaling to multiple tills per store.
- **Payment and Shopify failures:** A successful payment followed by a Shopify failure must not result in a lost sale or duplicate charge. The system should preserve the pending transaction and recover safely. A permanent Shopify rejection after payment requires an explicit reconciliation and refund procedure.
- **Day-end discrepancies:** During an outage, a paid bill may not yet appear in Shopify. Such transactions must be flagged as pending or mismatched rather than silently omitted from reconciliation.
- **Tax uncertainty:** Incorrect tax rates or discount allocation can cause invoice errors. Finance and the CA must approve the policy and rate data before production.
- **Speed versus correctness:** The 10-second target must not override prescription validation, payment confirmation, or durable recovery. Avoidable latency should be measured and optimised.
- **Remakes and family billing:** Both require additional financial, stock, and prescription-association rules. They should not be introduced until the relevant owners approve the workflows.
- **Production integration:** The exercise uses mock APIs. Real Shopify permissions, payment-terminal behaviour, authentication, rate limits, and outage handling must be validated before launch.
- **Launch timeline:** A simultaneous rollout to all seven stores by 15 November carries operational risk. A staged rollout provides an opportunity to validate billing, refunds, inventory, and reconciliation before expansion.

## 4. Dependencies for Production Launch

**Finance and CA:** Approved HSN tax rates and effective dates, discount allocation policy, invoice and credit-note rules, financial-year resets, reconciliation definitions, and manual-discount audit requirements.

**Retail:** Final offer stacking and precedence rules, manager permissions and limits, refund policy, pilot-store workflows, and acceptance criteria for billing speed.

**Optometry:** Approved prescription validation rules, axis handling, expiry and ADD requirements, external-prescription exceptions, and remake authorisation.

**Ops and Platform Engineering:** Production Shopify credentials and permissions, payment-terminal integration and idempotency support, authenticated prescription and staff APIs, inventory-location mapping, monitoring, outage recovery, and escalation procedures.

**Product and Data:** Decisions on family billing, remakes, sales channels, and the daily email, including clear owners and success measures.

**Release Team:** A pilot store, test scenarios covering payment failures and refunds, staff training, a rollback plan, and monitoring of billing performance and reconciliation.

## Recommendation

Deliver and validate the Must-have billing workflow first, using a pilot store with Shopify POS available as a fallback. Expand progressively only after payment processing, prescription validation, tax calculations, refunds, inventory updates, and day-end reconciliation pass agreed acceptance checks.

The 15 November target should remain the delivery goal, while the production rollout remains dependent on stakeholder decisions, successful pilot results, and operational readiness.
