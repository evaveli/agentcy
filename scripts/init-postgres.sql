-- Shared Postgres schema for business-domain demo agents
-- Seeded with realistic demo data for a warehouse brokerage

-- ─────────────────────────────────────────────────────────────────────
-- Tables
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS clients (
    id              SERIAL PRIMARY KEY,
    company_name    TEXT NOT NULL,
    industry        TEXT,
    contact_name    TEXT,
    contact_email   TEXT,
    contact_phone   TEXT,
    company_size    TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS client_requirements (
    id                   SERIAL PRIMARY KEY,
    client_id            INT REFERENCES clients(id),
    min_sqft             INT,
    max_sqft             INT,
    ceiling_height_ft    NUMERIC(5,1),
    dock_doors           INT,
    preferred_location   TEXT,
    max_monthly_budget   NUMERIC(12,2),
    lease_term_months    INT,
    move_in_date         DATE,
    special_requirements TEXT,
    priority             TEXT DEFAULT 'medium'
);

CREATE TABLE IF NOT EXISTS warehouses (
    id                    SERIAL PRIMARY KEY,
    name                  TEXT NOT NULL,
    address               TEXT,
    city                  TEXT,
    region                TEXT,
    total_sqft            INT,
    available_sqft        INT,
    ceiling_height_ft     NUMERIC(5,1),
    dock_doors            INT,
    monthly_rent_per_sqft NUMERIC(8,4),
    has_cold_storage      BOOLEAN DEFAULT false,
    has_hazmat            BOOLEAN DEFAULT false,
    security_level        TEXT DEFAULT 'standard',
    year_built            INT,
    status                TEXT DEFAULT 'available'
);

CREATE TABLE IF NOT EXISTS deals (
    id              SERIAL PRIMARY KEY,
    deal_name       TEXT NOT NULL,
    client_name     TEXT,
    deal_stage      TEXT DEFAULT 'prospecting',
    deal_value      NUMERIC(14,2),
    currency        TEXT DEFAULT 'EUR',
    start_date      DATE,
    expected_close  DATE,
    assigned_broker TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS emails (
    id            SERIAL PRIMARY KEY,
    deal_id       INT REFERENCES deals(id),
    sender        TEXT,
    recipient     TEXT,
    subject       TEXT,
    body_snippet  TEXT,
    sent_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notes (
    id          SERIAL PRIMARY KEY,
    deal_id     INT REFERENCES deals(id),
    author      TEXT,
    content     TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS calls (
    id               SERIAL PRIMARY KEY,
    caller_name      TEXT,
    caller_type      TEXT,       -- 'client' | 'supplier' | 'broker'
    phone            TEXT,
    call_date        TIMESTAMPTZ DEFAULT now(),
    duration_minutes INT,
    notes            TEXT,
    status           TEXT DEFAULT 'completed'
);

CREATE TABLE IF NOT EXISTS email_drafts (
    id              SERIAL PRIMARY KEY,
    deal_id         INT REFERENCES deals(id),
    email_type      TEXT NOT NULL,           -- 'intro' | 'quote_follow_up' | 'status_update'
    ai_generated    TEXT NOT NULL,           -- the AI-produced email body
    human_edited    TEXT,                    -- the version the user actually sent (NULL = not yet sent)
    edit_category   TEXT,                    -- 'none' | 'minor' | 'moderate' | 'major'
    sent            BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT now(),
    sent_at         TIMESTAMPTZ
);

-- ─────────────────────────────────────────────────────────────────────
-- Seed data
-- ─────────────────────────────────────────────────────────────────────

-- Clients
INSERT INTO clients (company_name, industry, contact_name, contact_email, contact_phone, company_size) VALUES
('FreshCo Logistics', 'Food & Beverage', 'Maria Rossi', 'maria@freshco.eu', '+39 02 1234 5678', '50-200'),
('TechParts GmbH', 'Electronics Manufacturing', 'Hans Mueller', 'hans@techparts.de', '+49 89 9876 5432', '200-500'),
('GreenLeaf Pharma', 'Pharmaceuticals', 'Sophie Dupont', 'sophie@greenleaf.fr', '+33 1 4567 8901', '500-1000'),
('QuickShip Express', 'E-commerce Fulfillment', 'Luca Bianchi', 'luca@quickship.it', '+39 06 2345 6789', '100-500'),
('Nordic Steel AB', 'Heavy Industry', 'Erik Lindgren', 'erik@nordicsteel.se', '+46 8 1234 5678', '1000+');

-- Client requirements
INSERT INTO client_requirements (client_id, min_sqft, max_sqft, ceiling_height_ft, dock_doors, preferred_location, max_monthly_budget, lease_term_months, move_in_date, special_requirements, priority) VALUES
(1, 15000, 25000, 24.0, 6, 'Milan metropolitan area', 18000.00, 36, '2026-06-01', 'Cold storage required, food-grade flooring, temperature monitoring', 'high'),
(2, 30000, 50000, 30.0, 10, 'Munich or Stuttgart region', 35000.00, 60, '2026-09-01', 'ESD flooring, clean room section (2000 sqft), heavy power (400A)', 'high'),
(3, 20000, 35000, 20.0, 4, 'Paris suburbs or Lyon', 28000.00, 48, '2026-07-15', 'GDP-compliant cold chain, hazmat storage, 24/7 security, backup power', 'critical'),
(4, 40000, 60000, 32.0, 12, 'Rome or Bologna corridor', 40000.00, 24, '2026-05-01', 'Mezzanine for packing stations, high-speed internet, office space 3000 sqft', 'medium'),
(5, 50000, 80000, 40.0, 8, 'Gothenburg port area', 55000.00, 60, '2026-10-01', 'Overhead crane (10 ton), reinforced flooring, rail siding access', 'medium');

-- Warehouses
INSERT INTO warehouses (name, address, city, region, total_sqft, available_sqft, ceiling_height_ft, dock_doors, monthly_rent_per_sqft, has_cold_storage, has_hazmat, security_level, year_built, status) VALUES
('LogisPark Milano Nord', 'Via Industriale 42', 'Milan', 'Lombardy', 45000, 22000, 28.0, 8, 0.75, true, false, 'enhanced', 2019, 'available'),
('Bavaria Logistics Hub', 'Industriestr. 15', 'Munich', 'Bavaria', 65000, 40000, 32.0, 14, 0.85, false, false, 'standard', 2021, 'available'),
('PharmaStore Lyon', 'Rue de la Logistique 8', 'Lyon', 'Auvergne-Rhone-Alpes', 30000, 28000, 22.0, 6, 0.95, true, true, 'high', 2020, 'available'),
('Centro Logistico Bologna', 'Via Emilia 156', 'Bologna', 'Emilia-Romagna', 55000, 55000, 34.0, 12, 0.65, false, false, 'standard', 2022, 'available'),
('Gothenburg Port Warehouse', 'Hamngatan 23', 'Gothenburg', 'Vastra Gotaland', 80000, 60000, 42.0, 10, 0.70, false, false, 'enhanced', 2018, 'available'),
('Stuttgart TechCenter', 'Technikweg 5', 'Stuttgart', 'Baden-Wurttemberg', 35000, 35000, 30.0, 8, 0.90, false, false, 'high', 2023, 'available'),
('Roma Sud Distribution', 'Via Pontina km 30', 'Rome', 'Lazio', 48000, 48000, 30.0, 10, 0.60, false, false, 'standard', 2017, 'available'),
('Paris CDG Logistics', 'Zone Industrielle Nord', 'Roissy-en-France', 'Ile-de-France', 40000, 25000, 24.0, 8, 1.10, true, true, 'high', 2021, 'available');

-- Deals
INSERT INTO deals (deal_name, client_name, deal_stage, deal_value, currency, start_date, expected_close, assigned_broker, notes) VALUES
('FreshCo Milan Expansion', 'FreshCo Logistics', 'negotiation', 648000.00, 'EUR', '2026-01-15', '2026-04-30', 'Alessandro Conti', 'Client needs cold storage urgently. Comparing LogisPark Milano Nord. Budget approved by CFO.'),
('TechParts Munich Relocation', 'TechParts GmbH', 'qualification', 2100000.00, 'EUR', '2026-02-01', '2026-07-15', 'Katrin Hoffmann', 'Current lease expires Sept. Need ESD flooring. Exploring Bavaria Hub and Stuttgart TechCenter.'),
('GreenLeaf GDP Warehouse', 'GreenLeaf Pharma', 'proposal', 1344000.00, 'EUR', '2026-01-20', '2026-05-30', 'Pierre Martin', 'Critical: GDP compliance mandatory. PharmaStore Lyon is primary candidate. Paris CDG as backup.'),
('QuickShip Fulfillment Center', 'QuickShip Express', 'prospecting', 960000.00, 'EUR', '2026-03-01', '2026-08-15', 'Alessandro Conti', 'New e-commerce client. Looking for large space with mezzanine. Bologna or Rome corridor.'),
('Nordic Steel Port Facility', 'Nordic Steel AB', 'negotiation', 2310000.00, 'EUR', '2026-02-10', '2026-06-30', 'Anna Svensson', 'Need crane and rail access. Gothenburg Port is ideal but need to confirm crane capacity.');

-- Emails
INSERT INTO emails (deal_id, sender, recipient, subject, body_snippet, sent_at) VALUES
-- Deal 1: FreshCo — full thread (intro → negotiation)
(1, 'a.conti@brokerage.eu', 'maria@freshco.eu', 'Introduction — Warehouse Solutions for FreshCo', 'Dear Maria, I''m Alessandro Conti from EuroLogis Brokerage. We specialize in cold-chain warehouse solutions across Northern Italy. I understand FreshCo is looking to expand its Milan operations — I''d love to discuss how we can help. Would you be available for a call this week?', '2026-01-16 10:00:00+01'),
(1, 'maria@freshco.eu', 'a.conti@brokerage.eu', 'RE: Introduction — Warehouse Solutions for FreshCo', 'Alessandro, thank you for reaching out. Yes, we are actively looking for cold storage in the Milan area — approximately 15,000-25,000 sqft with at least 6 dock doors. Let''s schedule a call for Thursday.', '2026-01-17 14:00:00+01'),
(1, 'a.conti@brokerage.eu', 'maria@freshco.eu', 'LogisPark Milano Nord — Proposal & Pricing', 'Maria, following our call, please find attached our proposal for LogisPark Milano Nord. Key terms: 22,000 sqft available, cold zone capable of -30C, 8 dock doors, EUR 0.75/sqft/month (EUR 16,500/month). Lease term: 36 months with renewal option. The landlord is offering a EUR 50,000 fit-out allowance for food-grade flooring. I''d appreciate your feedback by end of next week.', '2026-02-05 11:30:00+01'),
(1, 'maria@freshco.eu', 'a.conti@brokerage.eu', 'RE: LogisPark Milano Nord — Proposal & Pricing', 'Alessandro, we reviewed the LogisPark specs. The cold storage zone looks good but we need to confirm the temperature range goes down to -25C for our frozen goods line. Also, can we discuss the fit-out timeline?', '2026-02-20 14:30:00+01'),
(1, 'a.conti@brokerage.eu', 'maria@freshco.eu', 'RE: LogisPark Milano Nord — Proposal & Pricing', 'Maria, confirmed with property manager — the cold zone supports -30C. I''ll send the updated spec sheet. The fit-out would take approximately 4-6 weeks. Can we schedule a site visit next week?', '2026-02-21 09:15:00+01'),
-- Deal 2: TechParts — requirements exchange
(2, 'k.hoffmann@brokerage.eu', 'hans@techparts.de', 'Warehouse Options for TechParts Relocation', 'Dear Hans, following up on our initial conversation about your Munich relocation. I have identified two strong candidates: Bavaria Logistics Hub (Munich, 40,000 sqft available) and Stuttgart TechCenter (35,000 sqft, newer build with superior ESD infrastructure). I''d like to present a detailed comparison. Shall I send the full spec sheets?', '2026-02-10 09:00:00+01'),
(2, 'hans@techparts.de', 'k.hoffmann@brokerage.eu', 'ESD Requirements Document', 'Katrin, attached is our ESD flooring specification. We need Category 2 per IEC 61340-5-1. Also, the clean room must maintain ISO Class 7. Please confirm both facilities can meet these requirements before we proceed with site visits.', '2026-02-18 11:00:00+01'),
(2, 'k.hoffmann@brokerage.eu', 'hans@techparts.de', 'RE: ESD Requirements — Status Update', 'Hans, update on your requirements: Stuttgart TechCenter confirms full IEC 61340-5-1 Cat 2 compliance and ISO Class 7 clean room capability. Bavaria Hub would need a EUR 120,000 retrofit for the clean room section. I recommend we prioritize Stuttgart for the site visit. Munich visit can be secondary. Available dates?', '2026-02-24 10:00:00+01'),
-- Deal 3: GreenLeaf — urgent compliance thread
(3, 'sophie@greenleaf.fr', 'p.martin@brokerage.eu', 'GDP Compliance Checklist', 'Pierre, our quality team has prepared the GDP compliance checklist. The warehouse must have: temperature mapping validation, backup generator with <10s switchover, and EU GDP certification. This is non-negotiable given our May 15 audit.', '2026-02-22 16:45:00+01'),
(3, 'p.martin@brokerage.eu', 'sophie@greenleaf.fr', 'RE: GDP Compliance — PharmaStore Lyon Update', 'Sophie, good news: PharmaStore Lyon already holds EU GDP certification (renewed Jan 2026). They have confirmed: temperature mapping completed Q4 2025, backup generator with 8s switchover, and hazmat zone availability. I am fast-tracking the inspection with their facility manager. Timeline: inspection next week, certification transfer within 4 weeks. Paris CDG remains our backup option.', '2026-02-25 09:30:00+01'),
-- Deal 4: QuickShip — early stage
(4, 'a.conti@brokerage.eu', 'luca@quickship.it', 'Introduction — Fulfillment Center Solutions', 'Dear Luca, great speaking with you today. As discussed, EuroLogis specializes in high-throughput e-commerce fulfillment spaces across Italy. Based on your requirements (40,000-60,000 sqft, 12+ dock doors, mezzanine capability, Bologna-Rome corridor), I have two properties I''d like to present: Centro Logistico Bologna and Roma Sud Distribution. I''ll prepare a comparison for your review this week.', '2026-03-02 14:00:00+01'),
-- Deal 5: Nordic Steel — detailed negotiation
(5, 'erik@nordicsteel.se', 'a.svensson@brokerage.eu', 'Crane Specifications', 'Anna, we need minimum 10-ton overhead crane, preferably 15-ton for future growth. Rail siding must accommodate standard gauge (1435mm). Our production schedule requires 24/7 access.', '2026-02-25 10:30:00+01'),
(5, 'a.svensson@brokerage.eu', 'erik@nordicsteel.se', 'RE: Crane & Rail — Gothenburg Port Update', 'Erik, I have confirmed with Gothenburg Port Authority: the current crane at Hamngatan is 8-ton, but they can upgrade to 15-ton within 8 weeks. Cost included in first 2 years of lease (port authority subsidy). Rail siding is standard gauge, confirmed suitable. Monthly rent: EUR 0.70/sqft for 60,000 sqft = EUR 42,000/month on a 60-month lease. Shall we move forward with a formal proposal?', '2026-02-27 11:00:00+01');

-- Notes
INSERT INTO notes (deal_id, author, content, created_at) VALUES
(1, 'Alessandro Conti', 'Site visit scheduled for March 5. Client''s operations manager will attend. Need to confirm parking for 12 refrigerated trucks.', '2026-02-22 11:00:00+01'),
(2, 'Katrin Hoffmann', 'Stuttgart TechCenter has better ESD infrastructure but Munich is closer to client''s existing supply chain. Recommend presenting both with cost comparison.', '2026-02-19 15:30:00+01'),
(3, 'Pierre Martin', 'URGENT: GreenLeaf audit scheduled May 15. Warehouse must be operational and GDP-certified before that. Timeline is very tight.', '2026-02-23 09:00:00+01'),
(4, 'Alessandro Conti', 'Initial call with QuickShip. They process 5,000 orders/day peak season. Need high throughput dock area and automated sorting compatibility.', '2026-03-01 14:00:00+01'),
(5, 'Anna Svensson', 'Port authority confirmed rail siding availability. Crane installation would take 6-8 weeks if current one is insufficient. Client flexible on move-in if crane is confirmed.', '2026-02-26 16:00:00+01');

-- Calls
INSERT INTO calls (caller_name, caller_type, phone, call_date, duration_minutes, notes, status) VALUES
('Maria Rossi', 'client', '+39 02 1234 5678', '2026-02-20 10:00:00+01', 25, 'Discussed cold storage requirements. Client needs -25C minimum. 12 truck dock capacity critical. Budget firm at 18K/month. Wants 3-year lease with option to extend.', 'completed'),
('Hans Mueller', 'client', '+49 89 9876 5432', '2026-02-18 14:00:00+01', 35, 'Detailed discussion on ESD and clean room specs. Client sending formal requirements doc. Timeline: must be operational by Sept 1. Willing to invest in fit-out if lease is 5+ years.', 'completed'),
('Sophie Dupont', 'client', '+33 1 4567 8901', '2026-02-22 11:30:00+01', 40, 'Urgent call about GDP compliance. Audit in May — non-negotiable deadline. Need temperature-mapped warehouse. Backup power critical. Client authorized premium pricing for compliant space.', 'completed'),
('LogisPark Property Manager', 'supplier', '+39 02 8888 1111', '2026-02-21 15:00:00+01', 15, 'Confirmed cold zone specs: -30C capable, 8000 sqft cold area. Available from June 1. Landlord open to tenant fit-out allowance of 50K EUR for food-grade flooring upgrade.', 'completed'),
('Luca Bianchi', 'client', '+39 06 2345 6789', '2026-03-01 09:30:00+01', 20, 'Introductory call. E-commerce fulfillment — 5000 orders/day peak. Need mezzanine, packing stations, and fast internet. Exploring Bologna and Rome. Budget flexible if location is right.', 'completed'),
('Gothenburg Port Authority', 'supplier', '+46 31 555 7890', '2026-02-25 13:00:00+01', 30, 'Discussed rail siding capacity and crane specifications at Hamngatan facility. Current crane is 8-ton; upgrade to 15-ton feasible within 8 weeks. Port fees included in lease for first 2 years.', 'completed'),
('Erik Lindgren', 'client', '+46 8 1234 5678', '2026-02-26 10:00:00+01', 20, 'Follow-up on crane capacity. Client confirmed 10-ton minimum, 15-ton preferred. Needs rail access for incoming steel shipments. 24/7 operations, heavy floor load requirements.', 'completed'),
('Pierre Martin', 'broker', '+33 6 1234 5678', '2026-02-23 17:00:00+01', 15, 'Internal call with legal team about GDP warehouse certification timeline. Minimum 4 weeks for certification after fit-out. Must fast-track PharmaStore Lyon inspection.', 'completed');
