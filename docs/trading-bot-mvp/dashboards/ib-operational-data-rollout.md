# IB Operational Data Rollout Plan

This plan is review-only. Do not run it during market hours, and do not deploy
until the implementation and migration are approved.

## Pre-deployment gate

1. Confirm the market is closed and the bot is not submitting orders.
2. Review the complete source and SQL diff.
3. Run focused broker/dashboard tests, the full trading-bot test suite, and the
   dashboard test/lint/build commands.
4. Record current service health and outbox status.
5. Back up the production dashboard, outbox, metrics, and trades SQLite files.
6. Export the current Supabase table definitions and take a database backup.

## Forward migration

1. Apply `supabase-read-model.sql` with a privileged migration identity.
2. Verify the new nullable currency, execution, benchmark, and retention
   columns and indexes. Existing slippage rows must read as version 1 and
   invalid.
   Keep both operational-metric unique keys during the mixed-version rollout:
   `metric_id` for the new publisher and `(metric_name, timestamp)` for
   origin/main and older publishers.
3. Deploy the application only after schema validation succeeds.
4. Start the service through the normal approved operations process.
5. Perform read-only checks for CAD account values, USD QQQ fills and
   commissions, execution-ID uniqueness, position suppression, and outbox
   status.

SQLite migrations are additive and run during store initialization. The equity
downsampling transaction writes deterministic aggregate rows before deleting
eligible raw rows; SQLite rolls the whole transaction back on interruption.

## Rollback

1. Stop the newly deployed application through the approved operations process.
2. Restore the backed-up SQLite files as a unit.
3. Roll back application code.
4. Leave additive Supabase columns in place unless a reviewed database rollback
   explicitly removes them; older code ignores them.
5. If remote data must be restored, restore the Supabase backup before
   restarting publication.
6. Recheck that unpublished, failed, publishing, and dead-letter outbox rows
   remain present before service restart.

## Later cleanup migration

Only after every publisher has moved to `metric_id`, create and review a
separate migration that removes the legacy `(metric_name, timestamp)` unique
constraint/index. Do not combine that cleanup with this rollout.

## Post-deployment validation

- Observe the next paper-trading session without placing manual orders.
- Confirm one execution row per IB execution ID, including partial fills.
- Confirm `TRADE_CLOSED` does not add a second slippage metric.
- Confirm a flat position is published once per startup and once per transition.
- Confirm only old published outbox rows are pruned.
- Confirm recent/raw, five-minute, daily, and event-associated equity snapshots
  match the configured retention policy.
