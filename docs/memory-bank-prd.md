# Project brief

This is the foundation document. It answers: what is this project, who is it for, and what does it need to do?

Keep it short—one to two pages maximum. Include:

The product’s purpose in one or two sentences
The primary users and their core needs
The top three to five goals the product must achieve
Any hard constraints (compliance requirements, platform targets, budget limits)
Example:

```
# Project Brief

## Purpose
A multi-tenant SaaS tool that helps small law firms track client billing hours.

## Users
- Paralegals logging time entries
- Partners reviewing and approving invoices

## Goals
- Sub-second time entry submission
- Accurate invoice generation with PDF export
- Role-based access: paralegal vs. partner permissions

## Constraints
- Must be GDPR-compliant (EU clients)
- No native mobile app in v1
```

# Product context

This file explains why the product exists and what success looks like. It’s less about features and more about intent.

Include:

The problem being solved
How the product fits into the user’s workflow
What “done well” looks like from a user perspective
Key differentiators from alternatives

# System patterns

This is your architecture document. It tells the agent how the system is built so it doesn’t suggest patterns that break your design.

Include:

High-level architecture (monolith, microservices, serverless, etc.)
Key technology choices and the reasoning behind them
Data flow diagrams (described in text or ASCII)
Integration points with external services
Recurring design patterns you use (e.g., repository pattern, event sourcing)
Example snippet:

```
## Architecture
Next.js frontend (App Router) + Node.js API layer + PostgreSQL via Prisma ORM.
Auth handled by Kinde (OAuth2/OIDC). All API routes are protected by JWT verification middleware.

## Patterns
- Server components for data fetching; client components only for interactivity
- All DB access goes through service layer, never directly from route handlers
- Feature flags managed via Kinde feature flags API
```

# Tech context

This file covers your development environment and tooling. It prevents the agent from suggesting incompatible libraries or outdated syntax.

Include:

Language versions (Node 20, Python 3.12, etc.)
Framework versions and key dependencies
Build and deployment setup
Local development commands
Environment variable conventions

# Active context

This is the most frequently updated file. It captures what you’re working on right now—the current sprint, the active task, and any in-progress decisions.

Include:

Current focus area or feature being built
Recent decisions made and why
Known blockers or open questions
What the next step is
Update this file at the end of every session. It’s the bridge between yesterday’s work and today’s.

# Progress log

A running record of what’s been completed, what’s in progress, and what’s not started. Think of it as a lightweight changelog for the agent.

Structure it as three lists:

Done — completed features and tasks
In progress — currently active work
Not started — planned but not yet begun


# Architectural Decision log - ADR

Record significant technical decisions here, along with the reasoning and alternatives considered. This prevents the agent from re-litigating settled decisions and helps you remember why you made a choice six months later.

Decision	Chosen approach	Alternatives considered	Reasoning
Auth provider	Kinde	Auth0, Supabase Auth	Better pricing at scale, built-in feature flags, M2M support
ORM	Prisma	Drizzle, TypeORM	Team familiarity, strong TypeScript types
Deployment	Vercel	Railway, Fly.io	Zero-config Next.js deployment
