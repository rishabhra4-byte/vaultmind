"""Demo documents for the fictional company Northwind Labs.

Each entry: owner team, classification, minimum position, teams shared with, uploader,
tags, age in days, and the document body (markdown).
"""

from .models import Classification as C
from .models import Position as P

DOCS = [
    # ---------------------------------------------------------------- company-wide
    dict(
        title="Employee Handbook 2026",
        summary="Working hours, the hybrid office schedule, leave allowances, probation and notice periods, and the yearly learning budget.",
        team="Human Resources",
        cls=C.PUBLIC,
        uploader="vikram",
        tags=["handbook", "leave", "policy", "onboarding", "benefits"],
        age=40,
        body="""# Employee Handbook 2026

Welcome to Northwind Labs. This handbook explains how we work and what you can expect from the company.

## Working hours and hybrid work
Core collaboration hours are 11:00 to 16:00 IST. Teams follow a hybrid model: three days a week in the Bengaluru office (Tuesday to Thursday) and two days remote. Managers can approve fully remote weeks for travel or personal reasons.

## Leave policy
Every full-time employee receives 24 days of earned leave per year, accrued monthly, plus 12 days of combined sick and casual leave. Up to 10 unused earned leave days carry over to the next year. Parental leave is 26 weeks for birthing parents and 6 weeks for other parents. Apply for leave in the HR portal at least one week ahead for planned time off.

## Probation and notice period
New joiners have a three-month probation period. After confirmation the notice period is 60 days for Associate level and above, and 30 days for interns.

## Learning budget
Each employee has an annual learning budget of INR 40,000 for courses, books and conferences. Claim it through the expense process described in the Expense Reimbursement Policy.

## Who to ask
For anything not covered here, contact your HR partner or post in the #ask-hr channel.""",
    ),
    dict(
        title="Holiday Calendar 2026",
        summary="The 12 fixed public holidays and 2 floating holidays for the Bengaluru office in 2026.",
        team="Human Resources",
        cls=C.PUBLIC,
        uploader="neha",
        tags=["holidays", "calendar", "leave"],
        age=70,
        body="""# Holiday Calendar 2026 (Bengaluru office)

Northwind Labs observes 12 fixed public holidays and 2 floating holidays in 2026.

Fixed holidays: Republic Day (26 January), Ugadi (19 March), Good Friday (3 April), May Day (1 May), Independence Day (15 August), Ganesh Chaturthi (14 September), Gandhi Jayanti (2 October), Dussehra (20 October), Diwali (8 and 9 November), Kannada Rajyotsava (1 November) and Christmas (25 December).

Floating holidays: choose any two from the optional list in the HR portal, for example Holi, Eid al-Fitr, Onam or Pongal. Floating holidays must be applied for like regular leave.

The office is closed on fixed holidays. Production on-call rotations continue over holidays; on-call engineers receive a compensatory day off.""",
    ),
    dict(
        title="Code of Conduct",
        summary="How we expect everyone to behave: respect, protecting company information, conflicts of interest, gifts, and how to raise concerns.",
        team="Human Resources",
        cls=C.PUBLIC,
        uploader="vikram",
        tags=["conduct", "ethics", "policy"],
        age=90,
        body="""# Code of Conduct

We expect everyone at Northwind Labs to act with integrity, respect and care.

Treat colleagues with respect. Harassment, discrimination and bullying are not tolerated in any form, in the office, online or at company events.

Protect company and customer information. Share documents only with people who are authorised to see them, and never upload company data to personal accounts or unapproved tools.

Avoid conflicts of interest. Disclose any outside work, investments in competitors or vendors, or close relationships that could affect your decisions.

Gifts from vendors or customers above INR 2,000 in value must be declared to your manager.

Speak up. Report concerns to your manager, HR, or the anonymous ethics line. Retaliation against anyone who raises a concern in good faith is itself a violation of this code.""",
    ),
    dict(
        title="IT Security Basics",
        summary="Password manager, two-factor authentication, VPN, phishing and lost-device rules for every employee.",
        team="Engineering",
        cls=C.PUBLIC,
        uploader="dev",
        tags=["security", "it", "vpn", "onboarding", "laptop"],
        age=25,
        body="""# IT Security Basics

These rules apply to every employee and contractor.

Use the company password manager for every work account and turn on two-factor authentication everywhere it is offered. Never reuse a work password on a personal site.

Connect through the company VPN when working from outside the office. Production systems are only reachable over the VPN.

Lock your laptop whenever you step away. Laptops are encrypted and managed centrally; do not disable the endpoint protection agent.

Phishing: if an email asks you to log in, pay an invoice or share a document urgently, verify it first. Report suspicious messages in the #security channel; the security team responds within one business hour.

Lost or stolen devices must be reported to IT immediately so the device can be wiped remotely.""",
    ),
    dict(
        title="Expense Reimbursement Policy",
        summary="How to claim expenses, travel and meal limits, and who approves what.",
        team="Finance",
        cls=C.PUBLIC,
        uploader="ananya",
        tags=["expenses", "reimbursement", "travel", "policy"],
        age=55,
        body="""# Expense Reimbursement Policy

Submit expenses in the finance portal within 30 days of spending, with an itemised receipt for every claim above INR 500.

Travel: book flights and hotels through the company travel desk. Economy class for flights under six hours. Hotel limit is INR 7,000 per night in metro cities and INR 5,000 elsewhere.

Meals while travelling are reimbursed up to INR 1,500 per day. Team meals need prior manager approval.

Learning budget claims (courses, books, conferences) follow the same process; tag them as "Learning".

Approvals: your manager approves claims up to INR 25,000; anything above also needs Finance approval. Approved claims are paid with the next salary cycle.""",
    ),
    # ---------------------------------------------------------------- engineering
    dict(
        title="Backend Onboarding Guide",
        summary="Everything a new backend engineer needs for week one: access, local setup, our services and how code review works.",
        team="Engineering",
        cls=C.INTERNAL,
        uploader="rohan",
        tags=["onboarding", "backend", "setup", "engineering", "guide"],
        age=9,
        body="""# Backend Onboarding Guide

Welcome to the backend team! This guide gets you from a fresh laptop to your first merged pull request.

## Day 1: access and setup
Request access to the GitHub organisation, the #eng-backend channel and the staging VPN profile from your manager. Install Docker, Python 3.12, Node 20 and the internal CLI with `make bootstrap` in the platform-tools repository.

## Our services
- orders-api: FastAPI service that owns orders and carts. PostgreSQL database.
- payments-service: integrates with the payment gateway, idempotent webhooks, Redis for locks.
- notification-worker: consumes events from Kafka and sends email, SMS and push notifications.

Run everything locally with `docker compose up` from the dev-environment repository. Seed data is loaded automatically.

## Week 1
Pick a ticket labelled "good-first-issue" with your onboarding buddy. Read the API Design Guidelines and the System Architecture Overview before your first design discussion.

## Code review
Every change needs one approving review and green CI. Keep pull requests under 400 lines where possible, and write tests for new behaviour.""",
    ),
    dict(
        title="Intern Starter Pack",
        summary="What interns can expect: the first week, owning a project, mentor check-ins and the final presentation.",
        team="Engineering",
        cls=C.INTERNAL,
        uploader="rohan",
        tags=["intern", "onboarding", "starter", "guide", "mentorship"],
        age=6,
        body="""# Intern Starter Pack

Hello and welcome to your internship at Northwind Labs!

Your first week: meet your mentor, set up your laptop using the Backend Onboarding Guide, and attend the intern orientation on Monday at 11:00.

Your project: every intern owns one scoped project for the internship, agreed with their mentor by the end of week two. Past projects include a rate limiter for orders-api and a dashboard for notification delivery.

Check-ins: a 30-minute 1:1 with your mentor every week and a demo to the team in weeks 6 and 12.

Interns join code review and team rituals but are not part of the production on-call rotation.

At the end of the internship you present your project to the engineering team. Strong interns receive a pre-placement offer.""",
    ),
    dict(
        title="API Design Guidelines",
        summary="Conventions for every HTTP API: naming, versioning, errors, pagination and idempotency.",
        team="Engineering",
        cls=C.INTERNAL,
        uploader="priya",
        tags=["api", "rest", "design", "backend", "guidelines"],
        age=30,
        body="""# API Design Guidelines

All public and internal HTTP APIs follow these conventions.

Resources use plural nouns (`/orders`, `/orders/{id}/items`). Use HTTP verbs for actions; avoid verbs in paths except for genuine operations like `/payments/{id}/refund`.

Version APIs in the path (`/v1/`). Breaking changes require a new version and a deprecation window of at least 90 days.

Errors return JSON with `code`, `message` and an optional `details` object. Use 400 for validation errors, 401 when not authenticated, 403 when not allowed, 404 when not found, 409 for conflicts and 429 for rate limits.

Pagination is cursor-based: `?limit=50&cursor=...`, and responses include `next_cursor`.

Every write endpoint that can be retried must accept an `Idempotency-Key` header. Payment endpoints must reject requests without one.

Document every endpoint in the OpenAPI spec; CI fails if the spec and implementation diverge.""",
    ),
    dict(
        title="Incident Response Runbook",
        summary="Step-by-step response when production is degraded: severities, roles, mitigation and postmortems.",
        team="Engineering",
        cls=C.INTERNAL,
        min_pos=P.ASSOCIATE,
        uploader="rohan",
        tags=["incident", "on-call", "runbook", "production"],
        age=12,
        body="""# Incident Response Runbook

Use this runbook when production is degraded or down.

1. Acknowledge the page within 5 minutes. If you can't, escalate to the secondary on-call.
2. Declare the incident in #incidents with a severity: SEV1 (customers can't pay or order), SEV2 (major feature degraded), SEV3 (minor impact).
3. Assign an incident commander for SEV1 and SEV2. The commander coordinates; they do not debug.
4. Mitigate first: roll back the last deploy, fail over, or disable the feature flag. Root cause can wait.
5. Post status updates every 30 minutes for SEV1 and every hour for SEV2.
6. Write a blameless postmortem within 3 business days for every SEV1 and SEV2.

Production database access during incidents uses the break-glass role, which is logged and reviewed weekly.""",
    ),
    dict(
        title="System Architecture Overview",
        summary="How the platform fits together: services, databases, Kafka events, caching, search and observability.",
        team="Engineering",
        cls=C.INTERNAL,
        uploader="priya",
        tags=["architecture", "backend", "kafka", "postgresql", "design"],
        age=45,
        body="""# System Architecture Overview

Northwind Labs runs an event-driven architecture on Kubernetes.

Clients talk to an API gateway that handles authentication and rate limiting, then routes to services: orders-api, payments-service, catalog-service and user-service. Each service owns its PostgreSQL database; no service reads another service's tables.

Services publish domain events (OrderPlaced, PaymentCaptured, RefundIssued) to Kafka. The notification-worker and analytics pipeline consume these events.

Redis is used for caching and distributed locks. Search uses OpenSearch, fed by change-data-capture from catalog-service.

Observability: structured logs, metrics in Prometheus with Grafana dashboards, and distributed tracing with OpenTelemetry. Every service exposes /healthz and /readyz.

Deployments go through CI, then a canary release to 5% of traffic before full rollout.""",
    ),
    dict(
        title="Engineering Hiring Plan H1 2027",
        summary="Approved engineering roles, hiring budget and timeline for the first half of 2027.",
        team="Engineering",
        cls=C.CONFIDENTIAL,
        uploader="rohan",
        tags=["hiring", "headcount", "plan", "budget"],
        age=4,
        body="""# Engineering Hiring Plan H1 2027 (confidential)

Approved headcount for H1 2027 is 9 roles: 4 backend engineers (2 SDE-2, 2 Senior), 2 frontend engineers, 1 SRE, 1 engineering manager for the new payments team and 1 data engineer.

Budget: total hiring budget of INR 2.4 crore annualised compensation, plus INR 18 lakh for recruiting agency fees, approved by Finance.

Timeline: payments team roles open in January; SRE and data engineer in March.

Interview loops: one coding round, one system design round (Senior and above), one hiring-manager round. Interview panel training for new interviewers starts in December.

Do not share role counts or budgets outside the engineering management group.""",
    ),
    # ---------------------------------------------------------------- HR
    dict(
        title="Recruitment Process",
        summary="How hiring works from opening a role to sending the offer, and what interviewers must do.",
        team="Human Resources",
        cls=C.INTERNAL,
        shares=["Engineering", "Sales", "Finance"],
        uploader="neha",
        tags=["recruitment", "hiring", "interview", "process"],
        age=35,
        body="""# Recruitment Process

How we hire, from opening a role to the offer.

1. The hiring manager opens a requisition in the HR portal, which needs an approved headcount.
2. HR and the hiring manager agree on the job description and interview loop.
3. Recruiters screen candidates within 5 business days of applying.
4. Interviewers submit written feedback within 24 hours of each round, before discussing with other interviewers.
5. The debrief makes a hire or no-hire decision; the hiring manager has the final say.
6. HR prepares the offer within the approved salary band and sends it within 2 business days.

Employee referrals that result in a hire earn a referral bonus after the new joiner completes probation.""",
    ),
    dict(
        title="Performance Review Guidelines for Managers",
        summary="Review cycle dates, the rating scale, calibration targets and promotion rules for managers.",
        team="Human Resources",
        cls=C.CONFIDENTIAL,
        shares=["Engineering", "Finance", "Sales"],
        uploader="vikram",
        tags=["performance", "review", "managers", "ratings"],
        age=15,
        body="""# Performance Review Guidelines for Managers (confidential)

Reviews run twice a year, in April and October.

Ratings use a 5-point scale: 1 Below expectations, 2 Partially meets, 3 Meets, 4 Exceeds, 5 Outstanding. Calibration targets roughly 15% at 4 or above and no more than 10% at 1 or 2 per department.

Managers collect peer feedback from 3 to 5 colleagues and write a summary covering impact, collaboration and growth.

Ratings of 1 or 2 require a documented improvement plan reviewed by HR before the conversation.

Promotion nominations need evidence of operating at the next level for at least two quarters.

Do not share ratings or calibration outcomes with anyone outside the calibration group before the official release date.""",
    ),
    dict(
        title="Salary Bands 2026",
        summary="Fixed compensation ranges for every level in Engineering, Sales, Finance and HR, and who approves offers above them.",
        team="Human Resources",
        cls=C.CONFIDENTIAL,
        shares=["Engineering", "Finance", "Sales"],
        uploader="vikram",
        tags=["salary", "compensation", "bands", "pay"],
        age=20,
        body="""# Salary Bands 2026 (confidential)

Annual fixed compensation bands, INR lakh per annum.

Engineering: Intern stipend 0.8 lakh per month. SDE-1: 14 to 22. SDE-2: 22 to 34. Senior Engineer: 34 to 50. Staff Engineer: 50 to 70. Engineering Manager: 45 to 65.

Sales: Account Executive: 9 to 14 plus variable. Senior Account Executive: 14 to 22 plus variable. Sales Manager: 24 to 36 plus variable.

Finance and HR: Associate: 8 to 13. Manager: 20 to 32. Director: 40 to 60.

Offers above the midpoint of a band need Director approval. Offers above the band maximum need CEO approval.

Annual increments are decided after the October review cycle and take effect from 1 January.""",
    ),
    # ---------------------------------------------------------------- finance
    dict(
        title="Vendor Payment Process",
        summary="Vendor onboarding, purchase orders, payment terms and fraud checks for bank detail changes.",
        team="Finance",
        cls=C.INTERNAL,
        uploader="ananya",
        tags=["vendor", "payments", "invoices", "process"],
        age=50,
        body="""# Vendor Payment Process

All vendors must be onboarded in the finance system before the first purchase order, with GST registration and bank details verified.

Purchase orders are required for any spend above INR 50,000. Invoices without a matching purchase order are returned to the vendor.

Standard payment terms are 30 days from invoice receipt. Payments run every Wednesday and Friday.

Bank detail changes requested by a vendor must be confirmed by phone with a known contact before they are applied; this protects against invoice fraud.""",
    ),
    dict(
        title="Q3 2026 Financial Report",
        summary="Q3 revenue, margins, expenses, cash runway and key risks, prepared for the board.",
        team="Finance",
        cls=C.CONFIDENTIAL,
        min_pos=P.DIRECTOR,
        uploader="ananya",
        tags=["financials", "revenue", "quarterly", "report"],
        age=8,
        body="""# Q3 2026 Financial Report (confidential, board use)

Revenue for Q3 2026 was INR 48.2 crore, up 31% year on year. Gross margin improved to 64% from 59%, driven by lower payment processing costs.

Operating expenses were INR 39.5 crore, of which 58% was people cost. EBITDA was INR 3.1 crore, the first positive quarter.

Cash runway at current burn is 27 months. The board approved the H1 2027 hiring plan within the existing budget.

Risks: enterprise deal slippage into Q4 and rising cloud costs, which grew 18% quarter on quarter.""",
    ),
    # ---------------------------------------------------------------- sales
    dict(
        title="Sales Playbook",
        summary="Who we sell to, pipeline stages and exit criteria, discovery questions and demo tips.",
        team="Sales",
        cls=C.INTERNAL,
        uploader="meera",
        tags=["sales", "playbook", "customers", "pipeline"],
        age=28,
        body="""# Sales Playbook

Our ideal customers are mid-size online retailers with 50 to 500 employees and growing order volumes.

Pipeline stages: Lead, Discovery, Demo, Proposal, Negotiation, Closed. Move a deal forward only when its exit criteria are met; for example, Discovery needs a confirmed budget owner and timeline.

Discovery questions: How do you handle payment failures today? What does checkout downtime cost you per hour? Who signs off on new vendors?

Run demos with the customer's own catalogue where possible; the solutions team can prepare one with 48 hours' notice.

Log every customer interaction in the CRM the same day.""",
    ),
    dict(
        title="Pricing and Discount Approval Matrix",
        summary="List prices for each plan and the discount each role is allowed to approve.",
        team="Sales",
        cls=C.CONFIDENTIAL,
        shares=["Finance"],
        uploader="meera",
        tags=["pricing", "discount", "approval", "deals"],
        age=18,
        body="""# Pricing and Discount Approval Matrix (confidential)

List prices: Starter INR 25,000 per month, Growth INR 80,000 per month, Enterprise from INR 2.5 lakh per month.

Discount authority: Account Executives may offer up to 10%. Sales Managers up to 20%. Discounts above 20% need Finance Director approval, and above 30% need CEO approval.

Multi-year contracts may add 5% extra discount for prepayment. Never discount implementation fees.""",
    ),
    # ---------------------------------------------------------------- leadership (restricted)
    dict(
        title="Board Meeting Notes: Q3 2026",
        summary="Decisions and follow-ups from the Q3 2026 board meeting.",
        team="Leadership",
        cls=C.RESTRICTED,
        uploader="meera",
        tags=["board", "strategy", "minutes"],
        age=7,
        body="""# Board Meeting Notes: Q3 2026 (restricted)

Attendees: board members, CEO, CFO.

The board reviewed Q3 results and approved the H1 2027 hiring plan. Discussion of the proposed expansion into Southeast Asia was deferred to the next meeting pending market research.

The board asked management to present a cloud cost reduction plan targeting 15% savings by Q2 2027.

Next meeting: January 2027.""",
    ),
    dict(
        title="Project Falcon: Acquisition Evaluation",
        summary="Early evaluation of a possible acquisition. Need-to-know only.",
        team="Leadership",
        cls=C.RESTRICTED,
        uploader="meera",
        tags=["acquisition", "strategy", "falcon"],
        age=3,
        body="""# Project Falcon: Acquisition Evaluation (restricted)

Project Falcon evaluates acquiring a small logistics-tracking startup to add delivery visibility to our platform.

Status: early due diligence. Technical review of their codebase is scheduled for next month. No commitments have been made.

Strictly need-to-know. Do not discuss outside the deal team.""",
    ),
]
