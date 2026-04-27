"""Ground truth data structures for E1 evaluation, derived from init-postgres.sql seed data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


# ── Per-agent ground truth models ─────────────────────────────────────


@dataclass(frozen=True)
class EmailGroundTruth:
    deal_id: int
    email_type: Literal["intro", "quote_follow_up", "status_update"]
    expected_entities: dict[str, Any]
    expected_to: str
    expected_from: str
    expected_subject_keywords: list[str]
    body_must_contain: list[str]
    body_must_not_contain: list[str]
    human_reference_email: str
    expected_references_prior: bool
    deal_stage: str


@dataclass(frozen=True)
class DealSummaryGroundTruth:
    deal_id: int
    required_facts: dict[str, str]
    required_sections: list[str] = field(
        default_factory=lambda: [
            "deal_stage", "key_contacts", "financial_terms",
            "timeline", "risks", "next_steps",
        ]
    )
    structural_elements: list[str] = field(
        default_factory=lambda: ["summary_table", "risks_section", "next_steps"]
    )


@dataclass(frozen=True)
class NecessityFormGroundTruth:
    client_id: int
    expected_fields: dict[str, Any]
    critical_fields: list[str]
    acceptable_alternatives: dict[str, list[str]]


@dataclass(frozen=True)
class ProposalGroundTruth:
    deal_id: int
    required_sections: list[str]
    critical_facts: dict[str, str]
    forbidden_claims: list[str]


@dataclass(frozen=True)
class WarehouseGroundTruth:
    client_id: int
    correct_top1: str
    correct_top3: list[str]
    hard_constraints: dict[str, Any]
    client_location: str


# ── Seed data constants (from init-postgres.sql) ─────────────────────

DEALS = {
    1: {
        "deal_name": "FreshCo Milan Expansion",
        "client_name": "FreshCo Logistics",
        "deal_stage": "negotiation",
        "deal_value": 648000.00,
        "currency": "EUR",
        "start_date": "2026-01-15",
        "expected_close": "2026-04-30",
        "assigned_broker": "Alessandro Conti",
        "broker_email": "a.conti@brokerage.eu",
    },
    2: {
        "deal_name": "TechParts Munich Relocation",
        "client_name": "TechParts GmbH",
        "deal_stage": "qualification",
        "deal_value": 2100000.00,
        "currency": "EUR",
        "start_date": "2026-02-01",
        "expected_close": "2026-07-15",
        "assigned_broker": "Katrin Hoffmann",
        "broker_email": "k.hoffmann@brokerage.eu",
    },
    3: {
        "deal_name": "GreenLeaf GDP Warehouse",
        "client_name": "GreenLeaf Pharma",
        "deal_stage": "proposal",
        "deal_value": 1344000.00,
        "currency": "EUR",
        "start_date": "2026-01-20",
        "expected_close": "2026-05-30",
        "assigned_broker": "Pierre Martin",
        "broker_email": "p.martin@brokerage.eu",
    },
    4: {
        "deal_name": "QuickShip Fulfillment Center",
        "client_name": "QuickShip Express",
        "deal_stage": "prospecting",
        "deal_value": 960000.00,
        "currency": "EUR",
        "start_date": "2026-03-01",
        "expected_close": "2026-08-15",
        "assigned_broker": "Alessandro Conti",
        "broker_email": "a.conti@brokerage.eu",
    },
    5: {
        "deal_name": "Nordic Steel Port Facility",
        "client_name": "Nordic Steel AB",
        "deal_stage": "negotiation",
        "deal_value": 2310000.00,
        "currency": "EUR",
        "start_date": "2026-02-10",
        "expected_close": "2026-06-30",
        "assigned_broker": "Anna Svensson",
        "broker_email": "a.svensson@brokerage.eu",
    },
}

CLIENTS = {
    1: {
        "company_name": "FreshCo Logistics",
        "industry": "Food & Beverage",
        "contact_name": "Maria Rossi",
        "contact_email": "maria@freshco.eu",
        "contact_phone": "+39 02 1234 5678",
        "company_size": "50-200",
    },
    2: {
        "company_name": "TechParts GmbH",
        "industry": "Electronics Manufacturing",
        "contact_name": "Hans Mueller",
        "contact_email": "hans@techparts.de",
        "contact_phone": "+49 89 9876 5432",
        "company_size": "200-500",
    },
    3: {
        "company_name": "GreenLeaf Pharma",
        "industry": "Pharmaceuticals",
        "contact_name": "Sophie Dupont",
        "contact_email": "sophie@greenleaf.fr",
        "contact_phone": "+33 1 4567 8901",
        "company_size": "500-1000",
    },
    4: {
        "company_name": "QuickShip Express",
        "industry": "E-commerce Fulfillment",
        "contact_name": "Luca Bianchi",
        "contact_email": "luca@quickship.it",
        "contact_phone": "+39 06 2345 6789",
        "company_size": "100-500",
    },
    5: {
        "company_name": "Nordic Steel AB",
        "industry": "Heavy Industry",
        "contact_name": "Erik Lindgren",
        "contact_email": "erik@nordicsteel.se",
        "contact_phone": "+46 8 1234 5678",
        "company_size": "1000+",
    },
}

CLIENT_REQUIREMENTS = {
    1: {
        "min_sqft": 15000, "max_sqft": 25000, "ceiling_height_ft": 24.0,
        "dock_doors": 6, "preferred_location": "Milan metropolitan area",
        "max_monthly_budget": 18000.00, "lease_term_months": 36,
        "move_in_date": "2026-06-01",
        "special_requirements": "Cold storage required, food-grade flooring, temperature monitoring",
        "priority": "high",
    },
    2: {
        "min_sqft": 30000, "max_sqft": 50000, "ceiling_height_ft": 30.0,
        "dock_doors": 10, "preferred_location": "Munich or Stuttgart region",
        "max_monthly_budget": 35000.00, "lease_term_months": 60,
        "move_in_date": "2026-09-01",
        "special_requirements": "ESD flooring, clean room section (2000 sqft), heavy power (400A)",
        "priority": "high",
    },
    3: {
        "min_sqft": 20000, "max_sqft": 35000, "ceiling_height_ft": 20.0,
        "dock_doors": 4, "preferred_location": "Paris suburbs or Lyon",
        "max_monthly_budget": 28000.00, "lease_term_months": 48,
        "move_in_date": "2026-07-15",
        "special_requirements": "GDP-compliant cold chain, hazmat storage, 24/7 security, backup power",
        "priority": "critical",
    },
    4: {
        "min_sqft": 40000, "max_sqft": 60000, "ceiling_height_ft": 32.0,
        "dock_doors": 12, "preferred_location": "Rome or Bologna corridor",
        "max_monthly_budget": 40000.00, "lease_term_months": 24,
        "move_in_date": "2026-05-01",
        "special_requirements": "Mezzanine for packing stations, high-speed internet, office space 3000 sqft",
        "priority": "medium",
    },
    5: {
        "min_sqft": 50000, "max_sqft": 80000, "ceiling_height_ft": 40.0,
        "dock_doors": 8, "preferred_location": "Gothenburg port area",
        "max_monthly_budget": 55000.00, "lease_term_months": 60,
        "move_in_date": "2026-10-01",
        "special_requirements": "Overhead crane (10 ton), reinforced flooring, rail siding access",
        "priority": "medium",
    },
}

WAREHOUSES = {
    1: {
        "name": "LogisPark Milano Nord", "city": "Milan", "region": "Lombardy",
        "total_sqft": 45000, "available_sqft": 22000, "ceiling_height_ft": 28.0,
        "dock_doors": 8, "monthly_rent_per_sqft": 0.75,
        "has_cold_storage": True, "has_hazmat": False,
        "security_level": "enhanced", "year_built": 2019,
    },
    2: {
        "name": "Bavaria Logistics Hub", "city": "Munich", "region": "Bavaria",
        "total_sqft": 65000, "available_sqft": 40000, "ceiling_height_ft": 32.0,
        "dock_doors": 14, "monthly_rent_per_sqft": 0.85,
        "has_cold_storage": False, "has_hazmat": False,
        "security_level": "standard", "year_built": 2021,
    },
    3: {
        "name": "PharmaStore Lyon", "city": "Lyon", "region": "Auvergne-Rhone-Alpes",
        "total_sqft": 30000, "available_sqft": 28000, "ceiling_height_ft": 22.0,
        "dock_doors": 6, "monthly_rent_per_sqft": 0.95,
        "has_cold_storage": True, "has_hazmat": True,
        "security_level": "high", "year_built": 2020,
    },
    4: {
        "name": "Centro Logistico Bologna", "city": "Bologna", "region": "Emilia-Romagna",
        "total_sqft": 55000, "available_sqft": 55000, "ceiling_height_ft": 34.0,
        "dock_doors": 12, "monthly_rent_per_sqft": 0.65,
        "has_cold_storage": False, "has_hazmat": False,
        "security_level": "standard", "year_built": 2022,
    },
    5: {
        "name": "Gothenburg Port Warehouse", "city": "Gothenburg", "region": "Vastra Gotaland",
        "total_sqft": 80000, "available_sqft": 60000, "ceiling_height_ft": 42.0,
        "dock_doors": 10, "monthly_rent_per_sqft": 0.70,
        "has_cold_storage": False, "has_hazmat": False,
        "security_level": "enhanced", "year_built": 2018,
    },
    6: {
        "name": "Stuttgart TechCenter", "city": "Stuttgart", "region": "Baden-Wurttemberg",
        "total_sqft": 35000, "available_sqft": 35000, "ceiling_height_ft": 30.0,
        "dock_doors": 8, "monthly_rent_per_sqft": 0.90,
        "has_cold_storage": False, "has_hazmat": False,
        "security_level": "high", "year_built": 2023,
    },
    7: {
        "name": "Roma Sud Distribution", "city": "Rome", "region": "Lazio",
        "total_sqft": 48000, "available_sqft": 48000, "ceiling_height_ft": 30.0,
        "dock_doors": 10, "monthly_rent_per_sqft": 0.60,
        "has_cold_storage": False, "has_hazmat": False,
        "security_level": "standard", "year_built": 2017,
    },
    8: {
        "name": "Paris CDG Logistics", "city": "Roissy-en-France", "region": "Ile-de-France",
        "total_sqft": 40000, "available_sqft": 25000, "ceiling_height_ft": 24.0,
        "dock_doors": 8, "monthly_rent_per_sqft": 1.10,
        "has_cold_storage": True, "has_hazmat": True,
        "security_level": "high", "year_built": 2021,
    },
}


# ── Pre-built ground truth instances ─────────────────────────────────

def build_email_ground_truths() -> list[EmailGroundTruth]:
    """One ground truth per deal, covering different email types."""
    return [
        # Deal 1: FreshCo — quote follow-up (deal is in negotiation, proposal sent)
        EmailGroundTruth(
            deal_id=1,
            email_type="quote_follow_up",
            expected_entities={
                "client_name": "Maria Rossi",
                "company": "FreshCo Logistics",
                "deal_name": "FreshCo Milan Expansion",
                "locations": ["Milan", "LogisPark Milano Nord"],
                "volumes_or_specs": "22,000 sqft",
                "timeline": "2026-04-30",
                "pricing": "EUR 0.75/sqft/month",
                "key_requirements": ["cold storage", "-30C", "food-grade flooring"],
            },
            expected_to="maria@freshco.eu",
            expected_from="a.conti@brokerage.eu",
            expected_subject_keywords=["LogisPark", "follow-up", "proposal", "Milan"],
            body_must_contain=[
                "cold storage", "LogisPark", "Maria", "site visit",
            ],
            body_must_not_contain=[
                "GDP compliance", "ESD flooring", "crane", "rail siding",
            ],
            human_reference_email=(
                "Dear Maria,\n\n"
                "Following up on our proposal for LogisPark Milano Nord. As confirmed, "
                "the cold zone supports temperatures down to -30°C, meeting your frozen "
                "goods requirements. The landlord has also approved a €50,000 fit-out "
                "allowance for food-grade flooring.\n\n"
                "Key terms recap:\n"
                "- Space: 22,000 sqft\n"
                "- Rent: €0.75/sqft/month (€16,500/month)\n"
                "- Lease: 36 months with renewal option\n"
                "- Cold storage: -30°C capable, 8,000 sqft zone\n\n"
                "Could we schedule a site visit next week? Your operations manager "
                "would be welcome to join.\n\n"
                "Best regards,\nAlessandro Conti"
            ),
            expected_references_prior=True,
            deal_stage="negotiation",
        ),
        # Deal 2: TechParts — status update (qualification stage, requirements exchanged)
        EmailGroundTruth(
            deal_id=2,
            email_type="status_update",
            expected_entities={
                "client_name": "Hans Mueller",
                "company": "TechParts GmbH",
                "deal_name": "TechParts Munich Relocation",
                "locations": ["Munich", "Stuttgart", "Bavaria Logistics Hub", "Stuttgart TechCenter"],
                "volumes_or_specs": "30,000-50,000 sqft",
                "timeline": "2026-09-01",
                "pricing": "EUR 0.85-0.90/sqft",
                "key_requirements": ["ESD flooring", "clean room", "ISO Class 7"],
            },
            expected_to="hans@techparts.de",
            expected_from="k.hoffmann@brokerage.eu",
            expected_subject_keywords=["status", "update", "TechParts", "relocation"],
            body_must_contain=[
                "ESD", "Stuttgart TechCenter", "clean room", "ISO Class 7",
            ],
            body_must_not_contain=[
                "cold storage", "GDP", "crane", "rail", "FreshCo",
            ],
            human_reference_email=(
                "Dear Hans,\n\n"
                "Here is a status update on your Munich relocation search.\n\n"
                "Stuttgart TechCenter confirms full IEC 61340-5-1 Category 2 ESD compliance "
                "and ISO Class 7 clean room capability. Bavaria Logistics Hub in Munich "
                "would require a €120,000 retrofit for the clean room section.\n\n"
                "My recommendation: prioritize Stuttgart TechCenter for the site visit, "
                "with Munich as a secondary option. Both properties offer the space range "
                "you need (35,000-40,000 sqft available).\n\n"
                "Could you share available dates for site visits?\n\n"
                "Best regards,\nKatrin Hoffmann"
            ),
            expected_references_prior=True,
            deal_stage="qualification",
        ),
        # Deal 3: GreenLeaf — status update (proposal stage, urgent compliance)
        EmailGroundTruth(
            deal_id=3,
            email_type="status_update",
            expected_entities={
                "client_name": "Sophie Dupont",
                "company": "GreenLeaf Pharma",
                "deal_name": "GreenLeaf GDP Warehouse",
                "locations": ["Lyon", "PharmaStore Lyon", "Paris CDG"],
                "volumes_or_specs": "28,000 sqft",
                "timeline": "May 15 audit",
                "pricing": "EUR 0.95/sqft",
                "key_requirements": ["GDP compliance", "hazmat", "backup generator", "temperature mapping"],
            },
            expected_to="sophie@greenleaf.fr",
            expected_from="p.martin@brokerage.eu",
            expected_subject_keywords=["GDP", "compliance", "update", "PharmaStore", "Lyon"],
            body_must_contain=[
                "GDP", "PharmaStore Lyon", "temperature mapping", "backup generator",
            ],
            body_must_not_contain=[
                "ESD", "crane", "mezzanine", "FreshCo", "TechParts",
            ],
            human_reference_email=(
                "Dear Sophie,\n\n"
                "Update on the GDP-compliant warehouse search.\n\n"
                "PharmaStore Lyon already holds EU GDP certification (renewed January 2026). "
                "Confirmed capabilities:\n"
                "- Temperature mapping completed Q4 2025\n"
                "- Backup generator with 8-second switchover\n"
                "- Hazmat zone available\n"
                "- 28,000 sqft available\n\n"
                "I am fast-tracking the inspection with their facility manager. "
                "Timeline: inspection next week, certification transfer within 4 weeks — "
                "well ahead of your May 15 audit.\n\n"
                "Paris CDG Logistics remains our backup option.\n\n"
                "Best regards,\nPierre Martin"
            ),
            expected_references_prior=True,
            deal_stage="proposal",
        ),
        # Deal 4: QuickShip — intro email (prospecting stage, first contact just happened)
        EmailGroundTruth(
            deal_id=4,
            email_type="intro",
            expected_entities={
                "client_name": "Luca Bianchi",
                "company": "QuickShip Express",
                "deal_name": "QuickShip Fulfillment Center",
                "locations": ["Bologna", "Rome", "Centro Logistico Bologna", "Roma Sud Distribution"],
                "volumes_or_specs": "40,000-60,000 sqft",
                "timeline": "2026-05-01",
                "pricing": "EUR 0.60-0.65/sqft",
                "key_requirements": ["mezzanine", "dock doors", "high throughput", "e-commerce"],
            },
            expected_to="luca@quickship.it",
            expected_from="a.conti@brokerage.eu",
            expected_subject_keywords=["introduction", "fulfillment", "QuickShip", "warehouse"],
            body_must_contain=[
                "Bologna", "Rome", "fulfillment", "Luca",
            ],
            body_must_not_contain=[
                "cold storage", "GDP", "ESD", "crane", "hazmat",
            ],
            human_reference_email=(
                "Dear Luca,\n\n"
                "Great speaking with you today. As discussed, EuroLogis specializes in "
                "high-throughput e-commerce fulfillment spaces across Italy.\n\n"
                "Based on your requirements (40,000-60,000 sqft, 12+ dock doors, mezzanine "
                "capability, Bologna-Rome corridor), I have two strong candidates:\n"
                "- Centro Logistico Bologna: 55,000 sqft, 12 dock doors, €0.65/sqft\n"
                "- Roma Sud Distribution: 48,000 sqft, 10 dock doors, €0.60/sqft\n\n"
                "I will prepare a detailed comparison for your review this week.\n\n"
                "Best regards,\nAlessandro Conti"
            ),
            expected_references_prior=False,
            deal_stage="prospecting",
        ),
        # Deal 5: Nordic Steel — quote follow-up (negotiation, pricing discussed)
        EmailGroundTruth(
            deal_id=5,
            email_type="quote_follow_up",
            expected_entities={
                "client_name": "Erik Lindgren",
                "company": "Nordic Steel AB",
                "deal_name": "Nordic Steel Port Facility",
                "locations": ["Gothenburg", "Gothenburg Port Warehouse"],
                "volumes_or_specs": "60,000 sqft",
                "timeline": "2026-10-01",
                "pricing": "EUR 0.70/sqft/month",
                "key_requirements": ["overhead crane", "15-ton", "rail siding", "24/7 access"],
            },
            expected_to="erik@nordicsteel.se",
            expected_from="a.svensson@brokerage.eu",
            expected_subject_keywords=["Gothenburg", "proposal", "crane", "port"],
            body_must_contain=[
                "crane", "15-ton", "rail siding", "Gothenburg Port",
            ],
            body_must_not_contain=[
                "cold storage", "GDP", "ESD", "mezzanine", "FreshCo",
            ],
            human_reference_email=(
                "Dear Erik,\n\n"
                "Following up on the Gothenburg Port Warehouse proposal.\n\n"
                "I have confirmed with the Port Authority:\n"
                "- Crane: current 8-ton can be upgraded to 15-ton within 8 weeks\n"
                "- Cost: included in first 2 years of lease (port authority subsidy)\n"
                "- Rail siding: standard gauge (1,435mm), confirmed suitable\n"
                "- 24/7 access: available\n\n"
                "Proposed terms:\n"
                "- Space: 60,000 sqft\n"
                "- Rent: €0.70/sqft/month (€42,000/month)\n"
                "- Lease: 60 months\n\n"
                "Shall we move forward with a formal proposal?\n\n"
                "Best regards,\nAnna Svensson"
            ),
            expected_references_prior=True,
            deal_stage="negotiation",
        ),
    ]


def build_deal_summary_ground_truths() -> list[DealSummaryGroundTruth]:
    return [
        DealSummaryGroundTruth(
            deal_id=1,
            required_facts={
                "client_name": "FreshCo Logistics",
                "deal_stage": "negotiation",
                "deal_value": "648,000",
                "assigned_broker": "Alessandro Conti",
                "expected_close": "2026-04-30",
                "cold_storage": "-30C",
                "budget": "18,000",
            },
        ),
        DealSummaryGroundTruth(
            deal_id=2,
            required_facts={
                "client_name": "TechParts GmbH",
                "deal_stage": "qualification",
                "deal_value": "2,100,000",
                "assigned_broker": "Katrin Hoffmann",
                "expected_close": "2026-07-15",
                "esd": "IEC 61340",
                "clean_room": "ISO Class 7",
            },
        ),
        DealSummaryGroundTruth(
            deal_id=3,
            required_facts={
                "client_name": "GreenLeaf Pharma",
                "deal_stage": "proposal",
                "deal_value": "1,344,000",
                "assigned_broker": "Pierre Martin",
                "expected_close": "2026-05-30",
                "gdp_audit": "May 15",
                "compliance": "GDP",
            },
        ),
        DealSummaryGroundTruth(
            deal_id=4,
            required_facts={
                "client_name": "QuickShip Express",
                "deal_stage": "prospecting",
                "deal_value": "960,000",
                "assigned_broker": "Alessandro Conti",
                "expected_close": "2026-08-15",
                "throughput": "5,000 orders/day",
            },
        ),
        DealSummaryGroundTruth(
            deal_id=5,
            required_facts={
                "client_name": "Nordic Steel AB",
                "deal_stage": "negotiation",
                "deal_value": "2,310,000",
                "assigned_broker": "Anna Svensson",
                "expected_close": "2026-06-30",
                "crane": "10-ton",
                "rail": "standard gauge",
            },
        ),
    ]


def build_necessity_form_ground_truths() -> list[NecessityFormGroundTruth]:
    return [
        NecessityFormGroundTruth(
            client_id=cid,
            expected_fields={
                "company_name": CLIENTS[cid]["company_name"],
                "industry": CLIENTS[cid]["industry"],
                "contact_name": CLIENTS[cid]["contact_name"],
                "contact_email": CLIENTS[cid]["contact_email"],
                "min_sqft": CLIENT_REQUIREMENTS[cid]["min_sqft"],
                "max_sqft": CLIENT_REQUIREMENTS[cid]["max_sqft"],
                "ceiling_height_ft": CLIENT_REQUIREMENTS[cid]["ceiling_height_ft"],
                "dock_doors": CLIENT_REQUIREMENTS[cid]["dock_doors"],
                "preferred_location": CLIENT_REQUIREMENTS[cid]["preferred_location"],
                "max_monthly_budget": CLIENT_REQUIREMENTS[cid]["max_monthly_budget"],
                "lease_term_months": CLIENT_REQUIREMENTS[cid]["lease_term_months"],
                "move_in_date": CLIENT_REQUIREMENTS[cid]["move_in_date"],
                "special_requirements": CLIENT_REQUIREMENTS[cid]["special_requirements"],
                "priority": CLIENT_REQUIREMENTS[cid]["priority"],
            },
            critical_fields=[
                "company_name", "preferred_location", "min_sqft", "max_sqft",
                "max_monthly_budget", "special_requirements",
            ],
            acceptable_alternatives={
                "preferred_location": {
                    1: ["Milan", "Milan area", "Milano", "Lombardy"],
                    2: ["Munich", "Stuttgart", "Bavaria", "Baden-Wurttemberg"],
                    3: ["Paris", "Lyon", "Ile-de-France", "Auvergne-Rhone-Alpes"],
                    4: ["Rome", "Bologna", "Lazio", "Emilia-Romagna"],
                    5: ["Gothenburg", "Vastra Gotaland", "Gothenburg port"],
                }.get(cid, []),
            },
        )
        for cid in range(1, 6)
    ]


def build_proposal_ground_truths() -> list[ProposalGroundTruth]:
    return [
        ProposalGroundTruth(
            deal_id=1,
            required_sections=[
                "executive_summary", "client_requirements", "warehouse_option",
                "financial_terms", "location_advantages", "timeline", "next_steps",
            ],
            critical_facts={
                "client_name": "FreshCo Logistics",
                "warehouse": "LogisPark Milano Nord",
                "rent": "0.75",
                "cold_storage": "-30C",
                "available_sqft": "22,000",
                "lease_term": "36 months",
            },
            forbidden_claims=[
                "GDP certified", "hazmat", "ESD flooring", "crane", "rail siding",
            ],
        ),
        ProposalGroundTruth(
            deal_id=2,
            required_sections=[
                "executive_summary", "client_requirements", "warehouse_option",
                "financial_terms", "comparison_table", "timeline", "next_steps",
            ],
            critical_facts={
                "client_name": "TechParts GmbH",
                "option_1": "Bavaria Logistics Hub",
                "option_2": "Stuttgart TechCenter",
                "esd_requirement": "IEC 61340-5-1",
                "clean_room": "ISO Class 7",
            },
            forbidden_claims=[
                "cold storage", "GDP", "hazmat", "crane", "rail",
            ],
        ),
        ProposalGroundTruth(
            deal_id=3,
            required_sections=[
                "executive_summary", "client_requirements", "warehouse_option",
                "financial_terms", "compliance_section", "timeline", "next_steps",
            ],
            critical_facts={
                "client_name": "GreenLeaf Pharma",
                "warehouse": "PharmaStore Lyon",
                "gdp_certification": "EU GDP",
                "backup_power": "8s switchover",
                "hazmat": "available",
                "audit_deadline": "May 15",
            },
            forbidden_claims=[
                "ESD flooring", "crane", "mezzanine", "rail siding",
            ],
        ),
        ProposalGroundTruth(
            deal_id=4,
            required_sections=[
                "executive_summary", "client_requirements", "warehouse_option",
                "financial_terms", "comparison_table", "timeline", "next_steps",
            ],
            critical_facts={
                "client_name": "QuickShip Express",
                "option_1": "Centro Logistico Bologna",
                "option_2": "Roma Sud Distribution",
                "throughput": "5,000 orders/day",
            },
            forbidden_claims=[
                "cold storage", "GDP", "hazmat", "ESD", "crane",
            ],
        ),
        ProposalGroundTruth(
            deal_id=5,
            required_sections=[
                "executive_summary", "client_requirements", "warehouse_option",
                "financial_terms", "infrastructure_section", "timeline", "next_steps",
            ],
            critical_facts={
                "client_name": "Nordic Steel AB",
                "warehouse": "Gothenburg Port Warehouse",
                "crane": "15-ton",
                "rail_siding": "standard gauge",
                "rent": "0.70",
                "available_sqft": "60,000",
            },
            forbidden_claims=[
                "cold storage", "GDP", "ESD", "mezzanine", "food-grade",
            ],
        ),
    ]


def build_warehouse_ground_truths() -> list[WarehouseGroundTruth]:
    """Correct warehouse matches based on constraint analysis of seed data."""
    return [
        # Client 1 (FreshCo): needs cold storage, Milan, 15K-25K sqft, 6+ docks, <=18K/mo
        # LogisPark Milano Nord: cold=True, Milan, 22K sqft, 8 docks, 22K*0.75=16,500/mo ✓
        WarehouseGroundTruth(
            client_id=1,
            correct_top1="LogisPark Milano Nord",
            correct_top3=["LogisPark Milano Nord"],
            hard_constraints={
                "has_cold_storage": True,
                "min_available_sqft": 15000,
                "min_dock_doors": 6,
            },
            client_location="Milan",
        ),
        # Client 2 (TechParts): Munich/Stuttgart, 30K-50K sqft, 10+ docks, <=35K/mo
        # Bavaria Hub: Munich, 40K sqft, 14 docks, 40K*0.85=34,000/mo ✓
        # Stuttgart TechCenter: Stuttgart, 35K sqft, 8 docks (<10, fails dock constraint)
        WarehouseGroundTruth(
            client_id=2,
            correct_top1="Bavaria Logistics Hub",
            correct_top3=["Bavaria Logistics Hub", "Stuttgart TechCenter"],
            hard_constraints={
                "min_available_sqft": 30000,
                "min_dock_doors": 10,
            },
            client_location="Munich",
        ),
        # Client 3 (GreenLeaf): Paris/Lyon, 20K-35K sqft, cold+hazmat, <=28K/mo
        # PharmaStore Lyon: cold=True, hazmat=True, 28K sqft, 28K*0.95=26,600/mo ✓
        # Paris CDG: cold=True, hazmat=True, 25K sqft, 25K*1.10=27,500/mo ✓
        WarehouseGroundTruth(
            client_id=3,
            correct_top1="PharmaStore Lyon",
            correct_top3=["PharmaStore Lyon", "Paris CDG Logistics"],
            hard_constraints={
                "has_cold_storage": True,
                "has_hazmat": True,
                "min_available_sqft": 20000,
            },
            client_location="Lyon",
        ),
        # Client 4 (QuickShip): Rome/Bologna, 40K-60K sqft, 12+ docks, <=40K/mo
        # Bologna: 55K sqft, 12 docks, 55K*0.65=35,750/mo ✓
        # Roma Sud: 48K sqft, 10 docks (<12), 48K*0.60=28,800/mo — dock constraint fails
        WarehouseGroundTruth(
            client_id=4,
            correct_top1="Centro Logistico Bologna",
            correct_top3=["Centro Logistico Bologna", "Roma Sud Distribution"],
            hard_constraints={
                "min_available_sqft": 40000,
                "min_dock_doors": 12,
            },
            client_location="Rome",
        ),
        # Client 5 (Nordic Steel): Gothenburg, 50K-80K sqft, 8+ docks, <=55K/mo
        # Gothenburg Port: 60K sqft, 10 docks, 60K*0.70=42,000/mo ✓
        WarehouseGroundTruth(
            client_id=5,
            correct_top1="Gothenburg Port Warehouse",
            correct_top3=["Gothenburg Port Warehouse"],
            hard_constraints={
                "min_available_sqft": 50000,
                "min_dock_doors": 8,
            },
            client_location="Gothenburg",
        ),
    ]
