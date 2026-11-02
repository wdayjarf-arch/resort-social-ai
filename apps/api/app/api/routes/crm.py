"""Tenant-scoped customer CRM, export, Google sync and reviewed campaign endpoints."""
import csv, io, os
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import get_current_user, require_roles
from app.db.dependencies import get_db
from app.db.models.crm import CustomerLead, Campaign, CampaignRecipient
from app.db.models.identity import Membership, MembershipRole, User
from app.services.crm import segment_leads, export_vcf, normalize_phone, GooglePeopleAdapter

router = APIRouter(tags=["Resort CRM"])
READ = tuple(MembershipRole)
WRITE = (MembershipRole.owner, MembershipRole.admin, MembershipRole.manager, MembershipRole.agent)
ADMIN = (MembershipRole.owner, MembershipRole.admin, MembershipRole.manager)

class LeadInput(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = None
    email: EmailStr | None = None
    source_channel: str = "messenger"
    external_contact_id: str | None = None
    resort_interests: list[str] = Field(default_factory=list, max_length=20)
    booking_details: str | None = Field(default=None, max_length=1000)
    marketing_consent: bool = False
    consent_source: str | None = None

class CampaignInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    channel: str = Field(pattern="^(messenger|instagram|whatsapp|telegram)$")
    segment: dict = Field(default_factory=dict)
    template: str = Field(min_length=1, max_length=2000)

@router.get("/organizations/{organization_id}/customers")
def list_customers(organization_id: UUID, interest: str | None = None, lead_status: str | None = None,
                   inactive_days: int | None = Query(None, ge=1, le=3650), min_engagement_score: int | None = Query(None, ge=0, le=100),
                   limit: int = Query(100, ge=1, le=500), _: Membership = Depends(require_roles(*READ)), db: Session = Depends(get_db)):
    leads = segment_leads(db, organization_id, {"interest": interest, "lead_status": lead_status, "inactive_days": inactive_days, "min_engagement_score": min_engagement_score})[:limit]
    return {"items": [{"id": str(x.id), "full_name": x.full_name, "phone_e164": x.phone_e164, "email": x.email,
        "source_channel": x.source_channel, "resort_interests": x.resort_interests, "last_contacted_at": x.last_contacted_at,
        "guest_tone": x.guest_tone, "preferred_services": x.preferred_services, "sentiment_score": x.sentiment_score,
        "lead_status": x.lead_status, "engagement_score": x.engagement_score, "marketing_consent": x.marketing_consent,
        "transcript_summary": x.transcript_summary} for x in leads], "count": len(leads)}

@router.post("/organizations/{organization_id}/customers", status_code=201)
def create_customer(organization_id: UUID, body: LeadInput, _: Membership = Depends(require_roles(*WRITE)), db: Session = Depends(get_db)):
    if body.source_channel not in {"messenger", "instagram", "whatsapp", "telegram", "email", "manual"}: raise HTTPException(422, "Unsupported source channel")
    phone = normalize_phone(body.phone)
    if body.phone and not phone: raise HTTPException(422, "Phone number could not be normalized to E.164; include country code")
    lead = None
    if body.external_contact_id:
        lead = db.scalar(select(CustomerLead).where(CustomerLead.organization_id == organization_id, CustomerLead.source_channel == body.source_channel, CustomerLead.external_contact_id == body.external_contact_id))
    if lead is None and phone: lead = db.scalar(select(CustomerLead).where(CustomerLead.organization_id == organization_id, CustomerLead.phone_e164 == phone))
    if lead is None and body.email: lead = db.scalar(select(CustomerLead).where(CustomerLead.organization_id == organization_id, CustomerLead.email == str(body.email).lower()))
    if lead is None:
        lead = CustomerLead(organization_id=organization_id, source_channel=body.source_channel, external_contact_id=body.external_contact_id); db.add(lead)
    lead.full_name = body.full_name or lead.full_name; lead.phone_e164 = phone or lead.phone_e164
    lead.email = str(body.email).lower() if body.email else lead.email
    lead.resort_interests = sorted(set((lead.resort_interests or []) + body.resort_interests))
    if body.booking_details: lead.booking_history = (lead.booking_history or []) + [{"details": body.booking_details, "observed_at": datetime.now(timezone.utc).isoformat()}]; lead.lead_status = "hot"
    lead.last_contacted_at = datetime.now(timezone.utc)
    if body.marketing_consent:
        lead.marketing_consent = True; lead.consent_source = body.consent_source or "manual_api"; lead.consent_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(lead)
    return {"id": str(lead.id), "full_name": lead.full_name, "phone_e164": lead.phone_e164, "email": lead.email, "resort_interests": lead.resort_interests, "marketing_consent": lead.marketing_consent}

@router.get("/organizations/{organization_id}/customers/export.vcf")
def export_customers_vcf(organization_id: UUID, interest: str | None = None, _: Membership = Depends(require_roles(*READ)), db: Session = Depends(get_db)):
    leads = segment_leads(db, organization_id, {"interest": interest})
    return Response(export_vcf(leads), media_type="text/vcard; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="resort-customers.vcf"'})

@router.get("/organizations/{organization_id}/customers/export.csv")
def export_customers_csv(organization_id: UUID, _: Membership = Depends(require_roles(*READ)), db: Session = Depends(get_db)):
    leads = segment_leads(db, organization_id, {})
    out = io.StringIO(); w = csv.writer(out); w.writerow(["full_name","phone_e164","email","source_channel","interests","lead_status","marketing_consent"])
    for x in leads: w.writerow([x.full_name, x.phone_e164, x.email, x.source_channel, ";".join(x.resort_interests or []), x.lead_status, x.marketing_consent])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="resort-customers.csv"'})

@router.post("/organizations/{organization_id}/customers/{lead_id}/google-sync")
def google_sync(organization_id: UUID, lead_id: UUID, _: Membership = Depends(require_roles(*ADMIN)), db: Session = Depends(get_db)):
    lead = db.scalar(select(CustomerLead).where(CustomerLead.organization_id == organization_id, CustomerLead.id == lead_id))
    if not lead: raise HTTPException(404, "Customer not found")
    required = [os.getenv(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")]
    if not all(required): raise HTTPException(503, "Google People API OAuth is not configured")
    try:
        resource = GooglePeopleAdapter(*required).create_contact(lead)
    except Exception as exc: raise HTTPException(502, f"Google Contacts sync failed ({type(exc).__name__})") from exc
    lead.google_resource_name = resource; lead.google_synced_at = datetime.now(timezone.utc); db.commit()
    return {"status": "synced", "resource_name": resource}

@router.post("/organizations/{organization_id}/campaigns", status_code=201)
def create_campaign(organization_id: UUID, body: CampaignInput, current: User = Depends(get_current_user), _: Membership = Depends(require_roles(*WRITE)), db: Session = Depends(get_db)):
    leads = segment_leads(db, organization_id, body.segment)
    # Campaign recipients are only created for explicit marketing consent; no scraping/unsolicited outreach.
    eligible = [x for x in leads if x.marketing_consent and ((body.channel == "whatsapp" and x.phone_e164) or (body.channel in {"messenger", "instagram", "telegram"} and x.external_contact_id and x.source_channel == body.channel))]
    c = Campaign(organization_id=organization_id, name=body.name, channel=body.channel, segment=body.segment, template=body.template, status="pending_approval", created_by_user_id=current.id); db.add(c); db.flush()
    for lead in eligible:
        message = body.template.replace("{{name}}", lead.full_name or "Guest")
        db.add(CampaignRecipient(campaign_id=c.id, lead_id=lead.id, personalized_message=message, status="pending_review"))
    db.commit()
    return {"campaign_id": str(c.id), "status": c.status, "eligible_recipients": len(eligible), "excluded_without_consent": len(leads)-len(eligible), "message": "Recipients are staged for human review; nothing has been sent."}

@router.get("/organizations/{organization_id}/campaigns")
def list_campaigns(organization_id: UUID, _: Membership = Depends(require_roles(*READ)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Campaign).where(Campaign.organization_id == organization_id).order_by(Campaign.created_at.desc()).limit(100)).all()
    return [{"id": str(c.id), "name": c.name, "channel": c.channel, "status": c.status, "created_at": c.created_at,
             "recipients": db.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == c.id).count()} for c in rows]

@router.post("/organizations/{organization_id}/campaigns/{campaign_id}/approve")
def approve_campaign(organization_id: UUID, campaign_id: UUID, current: User = Depends(get_current_user), _: Membership = Depends(require_roles(*ADMIN)), db: Session = Depends(get_db)):
    c = db.scalar(select(Campaign).where(Campaign.organization_id == organization_id, Campaign.id == campaign_id).with_for_update())
    if not c: raise HTTPException(404, "Campaign not found")
    if c.status != "pending_approval": raise HTTPException(409, "Campaign is not awaiting approval")
    c.status = "approved"; c.approved_by_user_id = current.id; c.approved_at = datetime.now(timezone.utc)
    # Approval queues work, but delivery remains disabled until a channel-specific sender is configured.
    from app.workers.tasks import queue_approved_campaign
    db.commit(); queue_approved_campaign.delay(str(c.id))
    return {"campaign_id": str(c.id), "status": "approved", "message": "Approved recipients are queued for dispatch; delivery depends on valid provider credentials and channel policy checks."}
