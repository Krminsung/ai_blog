"""security_operations_ga

Revision ID: 657df178a4f3
Revises: 552b43678b02
Create Date: 2026-08-23 16:52:59.669491
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '657df178a4f3'
down_revision: str | None = '552b43678b02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SECURITY_TABLES = (
    'security_compliance_assessments',
    'security_copyright_cases',
    'security_incidents',
    'security_legal_holds',
    'security_privacy_access_events',
    'security_privacy_consent_evidence',
    'security_retention_policy_versions',
    'security_subprocessor_versions',
    'security_breach_notifications',
    'security_copyright_case_events',
    'security_copyright_counter_notices',
    'security_incident_events',
    'security_legal_hold_events',
    'security_privacy_requests',
    'security_retention_sweeps',
    'security_deletion_certificates',
    'security_privacy_actions',
    'security_privacy_export_artifacts',
    'security_privacy_verification_events',
    'security_provider_deletion_events',
    'security_retention_disposition_evidence',
    'security_backup_erasure_evidence',
    'security_privacy_action_attempts',
)

SECURITY_IMMUTABLE_TABLES = (
    'security_retention_policy_versions',
    'security_legal_hold_events',
    'security_retention_disposition_evidence',
    'security_provider_deletion_events',
    'security_privacy_verification_events',
    'security_privacy_action_attempts',
    'security_privacy_export_artifacts',
    'security_deletion_certificates',
    'security_backup_erasure_evidence',
    'security_privacy_access_events',
    'security_privacy_consent_evidence',
    'security_subprocessor_versions',
    'security_copyright_counter_notices',
    'security_copyright_case_events',
    'security_incident_events',
    'security_breach_notifications',
    'security_compliance_assessments',
)

SECURITY_MUTABLE_TABLES = tuple(
    table_name
    for table_name in SECURITY_TABLES
    if table_name not in SECURITY_IMMUTABLE_TABLES
)

OPERATIONS_TABLES = (
    'operations_backup_policy_versions',
    'operations_ga_assessments',
    'operations_runbook_versions',
    'operations_service_components',
    'operations_backup_runs',
    'operations_ga_gate_evidence',
    'operations_health_observations',
    'operations_incidents',
    'operations_backup_evidence',
    'operations_incident_events',
    'operations_status_notification_evidence',
    'operations_recovery_exercises',
    'operations_recovery_evidence',
)

OPERATIONS_IMMUTABLE_TABLES = (
    'operations_health_observations',
    'operations_incident_events',
    'operations_status_notification_evidence',
    'operations_runbook_versions',
    'operations_backup_policy_versions',
    'operations_backup_evidence',
    'operations_recovery_evidence',
    'operations_ga_gate_evidence',
)

OPERATIONS_MUTABLE_TABLES = tuple(
    table_name
    for table_name in OPERATIONS_TABLES
    if table_name not in OPERATIONS_IMMUTABLE_TABLES
)

OPERATIONS_WORKER_TABLES = (
    'operations_backup_runs',
    'operations_backup_evidence',
    'operations_recovery_exercises',
    'operations_recovery_evidence',
    'operations_runbook_versions',
    'operations_ga_assessments',
    'operations_ga_gate_evidence',
)

IMMUTABLE_TABLES = SECURITY_IMMUTABLE_TABLES + OPERATIONS_IMMUTABLE_TABLES
STAGE9_TABLES = SECURITY_TABLES + OPERATIONS_TABLES

HISTORY_FROZEN_FIELDS = {
    'security_legal_holds': (
        'id',
        'workspace_id',
        'external_matter_ref',
        'title',
        'reason',
        'scope_snapshot',
        'scope_hash',
        'evidence_object_refs',
        'activated_by',
        'activated_at',
        'expires_at',
        'created_at',
    ),
    'security_retention_sweeps': (
        'id',
        'workspace_id',
        'policy_version_id',
        'policy_snapshot',
        'policy_snapshot_hash',
        'legal_hold_snapshot',
        'legal_hold_snapshot_hash',
        'idempotency_key',
        'requested_by',
        'created_at',
    ),
    'security_privacy_requests': (
        'id',
        'workspace_id',
        'requested_by',
        'kind',
        'source',
        'external_request_ref',
        'idempotency_key',
        'request_hash',
        'subject_locator_ref',
        'subject_locator_hash',
        'data_classes',
        'requested_correction_ref',
        'requester_relationship',
        'retention_policy_version_id',
        'retention_policy_snapshot',
        'due_at',
        'created_at',
    ),
    'security_privacy_actions': (
        'id',
        'workspace_id',
        'request_id',
        'sequence',
        'kind',
        'data_classes',
        'target_system',
        'target_locator_ref',
        'plan_metadata',
        'plan_hash',
        'idempotency_key',
        'created_at',
    ),
    'security_copyright_cases': (
        'id',
        'workspace_id',
        'reported_by',
        'idempotency_key',
        'claimant_contact_ref',
        'claimant_contact_hash',
        'work_description',
        'target_refs',
        'evidence_object_refs',
        'sworn_statement',
        'request_hash',
        'created_at',
    ),
    'security_incidents': (
        'id',
        'workspace_id',
        'external_ref',
        'title',
        'incident_type',
        'severity',
        'detected_at',
        'detection_source',
        'runbook_version',
        'incident_policy_version',
        'impact_snapshot',
        'affected_data_classes',
        'containment_due_at',
        'notification_due_at',
        'opened_by',
        'created_at',
    ),
    'operations_service_components': (
        'id',
        'component_key',
        'kind',
        'created_by',
        'created_at',
    ),
    'operations_incidents': (
        'id',
        'external_ref',
        'title',
        'severity',
        'component_ids',
        'affected_workspace_ids',
        'started_at',
        'runbook_version_id',
        'opened_by',
        'created_at',
    ),
    'operations_backup_runs': (
        'id',
        'policy_version_id',
        'policy_snapshot',
        'policy_snapshot_hash',
        'idempotency_key',
        'requested_by',
        'requested_at',
        'created_at',
    ),
    'operations_recovery_exercises': (
        'id',
        'backup_evidence_id',
        'runbook_version_id',
        'rpo_minutes',
        'rto_minutes',
        'idempotency_key',
        'requested_by',
        'requested_at',
        'created_at',
    ),
    'operations_ga_assessments': (
        'id',
        'release_ref',
        'artifact_refs',
        'request_hash',
        'idempotency_key',
        'requested_by',
        'requested_at',
        'created_at',
    ),
}


def _qualified_tables(table_names: Sequence[str]) -> str:
    return ', '.join(f'public.{table_name}' for table_name in table_names)


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('operations_backup_policy_versions',
    sa.Column('policy_key', sa.String(length=160), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('data_scope', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('rpo_minutes', sa.Integer(), nullable=False),
    sa.Column('rto_minutes', sa.Integer(), nullable=False),
    sa.Column('backup_interval_minutes', sa.Integer(), nullable=False),
    sa.Column('pitr_enabled', sa.Boolean(), nullable=False),
    sa.Column('encrypted', sa.Boolean(), nullable=False),
    sa.Column('encryption_key_policy', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('retention_cycles', sa.Integer(), nullable=False),
    sa.Column('quarterly_drill_required', sa.Boolean(), nullable=False),
    sa.Column('region_policy', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('policy_hash', sa.String(length=64), nullable=False),
    sa.Column('effective_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_by', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('backup_interval_minutes > 0', name=op.f('ck_operations_backup_policy_versions_interval_positive')),
    sa.CheckConstraint('rpo_minutes > 0', name=op.f('ck_operations_backup_policy_versions_rpo_positive')),
    sa.CheckConstraint('rto_minutes > 0', name=op.f('ck_operations_backup_policy_versions_rto_positive')),
    sa.CheckConstraint('version > 0', name=op.f('ck_operations_backup_policy_versions_version_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_backup_policy_versions')),
    sa.UniqueConstraint('policy_key', 'version', name='operations_backup_policy_version')
    )
    op.create_index('ix_operations_backup_policy_effective', 'operations_backup_policy_versions', ['policy_key', 'effective_at'], unique=False)
    op.create_table('operations_ga_assessments',
    sa.Column('release_ref', sa.String(length=500), nullable=False),
    sa.Column('artifact_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('requested_by', sa.Uuid(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('decision_hash', sa.String(length=64), nullable=True),
    sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_code', sa.String(length=120), nullable=True),
    sa.Column('lock_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('attempt_count >= 0', name=op.f('ck_operations_ga_assessments_attempt_nonnegative')),
    sa.CheckConstraint('lock_version > 0', name=op.f('ck_operations_ga_assessments_lock_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_ga_assessments')),
    sa.UniqueConstraint('idempotency_key', name='operations_ga_idempotency'),
    sa.UniqueConstraint('release_ref', name='operations_ga_release_once')
    )
    op.create_index('ix_operations_ga_state', 'operations_ga_assessments', ['state', 'requested_at'], unique=False)
    op.create_table('operations_runbook_versions',
    sa.Column('runbook_key', sa.String(length=160), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('artifact_ref', sa.String(length=1000), nullable=False),
    sa.Column('artifact_hash', sa.String(length=64), nullable=False),
    sa.Column('owner_team', sa.String(length=160), nullable=False),
    sa.Column('escalation_policy_ref', sa.String(length=1000), nullable=False),
    sa.Column('exercise_interval_days', sa.Integer(), nullable=False),
    sa.Column('approved_by', sa.Uuid(), nullable=False),
    sa.Column('effective_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('version > 0', name=op.f('ck_operations_runbook_versions_version_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_runbook_versions')),
    sa.UniqueConstraint('runbook_key', 'version', name='operations_runbook_version')
    )
    op.create_index('ix_operations_runbook_effective', 'operations_runbook_versions', ['runbook_key', 'effective_at'], unique=False)
    op.create_table('operations_service_components',
    sa.Column('component_key', sa.String(length=160), nullable=False),
    sa.Column('display_name', sa.String(length=240), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('endpoint_ref', sa.String(length=1000), nullable=False),
    sa.Column('public', sa.Boolean(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('owner_team', sa.String(length=160), nullable=False),
    sa.Column('service_tier', sa.String(length=40), nullable=False),
    sa.Column('safe_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('lock_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('lock_version > 0', name=op.f('ck_operations_service_components_lock_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_service_components')),
    sa.UniqueConstraint('component_key', name='operations_component_key')
    )
    op.create_index('ix_operations_component_public', 'operations_service_components', ['public', 'enabled'], unique=False)
    op.create_table('security_compliance_assessments',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('artifact_ref', sa.String(length=1000), nullable=False),
    sa.Column('artifact_hash', sa.String(length=64), nullable=False),
    sa.Column('control_version', sa.String(length=80), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('verifier', sa.String(length=160), nullable=False),
    sa.Column('findings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('requested_by', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_compliance_assessments')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_assessment_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'kind', 'artifact_hash', 'control_version', name='security_assessment_evidence')
    )
    op.create_index('ix_security_assessment_expiry', 'security_compliance_assessments', ['workspace_id', 'kind', 'expires_at'], unique=False)
    op.create_index(op.f('ix_security_compliance_assessments_workspace_id'), 'security_compliance_assessments', ['workspace_id'], unique=False)
    op.create_table('security_copyright_cases',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('reported_by', sa.Uuid(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('claimant_contact_ref', sa.String(length=1000), nullable=False),
    sa.Column('claimant_contact_hash', sa.String(length=64), nullable=False),
    sa.Column('work_description', sa.Text(), nullable=False),
    sa.Column('target_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('evidence_object_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('sworn_statement', sa.Boolean(), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('response_due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('temporary_action', sa.String(length=80), nullable=True),
    sa.Column('policy_version', sa.String(length=80), nullable=True),
    sa.Column('counter_notice_received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_code', sa.String(length=120), nullable=True),
    sa.Column('lock_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('lock_version > 0', name=op.f('ck_security_copyright_cases_lock_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_copyright_cases')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_copyright_case_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'reported_by', 'idempotency_key', name='security_copyright_idempotency')
    )
    op.create_index(op.f('ix_security_copyright_cases_reported_by'), 'security_copyright_cases', ['reported_by'], unique=False)
    op.create_index(op.f('ix_security_copyright_cases_workspace_id'), 'security_copyright_cases', ['workspace_id'], unique=False)
    op.create_index('ix_security_copyright_sla', 'security_copyright_cases', ['workspace_id', 'state', 'response_due_at'], unique=False)
    op.create_table('security_incidents',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('external_ref', sa.String(length=500), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('incident_type', sa.String(length=120), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('state', sa.String(length=24), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('detection_source', sa.String(length=160), nullable=False),
    sa.Column('runbook_version', sa.String(length=80), nullable=False),
    sa.Column('incident_policy_version', sa.String(length=80), nullable=False),
    sa.Column('impact_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('affected_data_classes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('affected_subject_count', sa.Integer(), nullable=True),
    sa.Column('containment_due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('notification_due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('contained_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('opened_by', sa.Uuid(), nullable=False),
    sa.Column('lock_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('lock_version > 0', name=op.f('ck_security_incidents_lock_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_incidents')),
    sa.UniqueConstraint('workspace_id', 'external_ref', name='security_incident_external'),
    sa.UniqueConstraint('workspace_id', 'id', name='security_incident_workspace_id')
    )
    op.create_index('ix_security_incident_state', 'security_incidents', ['workspace_id', 'severity', 'state'], unique=False)
    op.create_index(op.f('ix_security_incidents_workspace_id'), 'security_incidents', ['workspace_id'], unique=False)
    op.create_table('security_legal_holds',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('external_matter_ref', sa.String(length=500), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('scope_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('scope_hash', sa.String(length=64), nullable=False),
    sa.Column('evidence_object_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('activated_by', sa.Uuid(), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('released_by', sa.Uuid(), nullable=True),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('release_reason', sa.Text(), nullable=True),
    sa.Column('lock_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('lock_version > 0', name=op.f('ck_security_legal_holds_lock_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_legal_holds')),
    sa.UniqueConstraint('workspace_id', 'external_matter_ref', name='security_legal_hold_matter'),
    sa.UniqueConstraint('workspace_id', 'id', name='security_legal_hold_workspace_id')
    )
    op.create_index('ix_security_legal_hold_active', 'security_legal_holds', ['workspace_id', 'state', 'expires_at'], unique=False)
    op.create_index(op.f('ix_security_legal_holds_workspace_id'), 'security_legal_holds', ['workspace_id'], unique=False)
    op.create_table('security_privacy_access_events',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('action', sa.String(length=120), nullable=False),
    sa.Column('subject_type', sa.String(length=80), nullable=False),
    sa.Column('subject_id', sa.String(length=500), nullable=False),
    sa.Column('data_classes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('purpose', sa.String(length=240), nullable=False),
    sa.Column('bulk', sa.Boolean(), nullable=False),
    sa.Column('watermark_reference', sa.String(length=500), nullable=True),
    sa.Column('delivery_reference', sa.String(length=500), nullable=True),
    sa.Column('request_id', sa.String(length=120), nullable=True),
    sa.Column('ip_hash', sa.String(length=64), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_privacy_access_events')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_privacy_access_workspace_id')
    )
    op.create_index(op.f('ix_security_privacy_access_events_actor_id'), 'security_privacy_access_events', ['actor_id'], unique=False)
    op.create_index(op.f('ix_security_privacy_access_events_workspace_id'), 'security_privacy_access_events', ['workspace_id'], unique=False)
    op.create_index('ix_security_privacy_access_subject', 'security_privacy_access_events', ['workspace_id', 'subject_type', 'subject_id'], unique=False)
    op.create_index('ix_security_privacy_access_time', 'security_privacy_access_events', ['workspace_id', 'occurred_at'], unique=False)
    op.create_table('security_privacy_consent_evidence',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('subject_id', sa.Uuid(), nullable=False),
    sa.Column('purpose', sa.String(length=40), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('policy_version', sa.String(length=80), nullable=False),
    sa.Column('policy_hash', sa.String(length=64), nullable=False),
    sa.Column('scope_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('transfer_countries', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('supersedes_id', sa.Uuid(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_privacy_consent_evidence')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_consent_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'subject_id', 'purpose', 'policy_version', 'idempotency_key', name='security_consent_idempotency'),
    sa.UniqueConstraint('workspace_id', 'supersedes_id', name='security_consent_single_successor')
    )
    op.create_index('ix_security_consent_subject', 'security_privacy_consent_evidence', ['workspace_id', 'subject_id', 'purpose', 'occurred_at'], unique=False)
    op.create_index(op.f('ix_security_privacy_consent_evidence_subject_id'), 'security_privacy_consent_evidence', ['subject_id'], unique=False)
    op.create_index(op.f('ix_security_privacy_consent_evidence_supersedes_id'), 'security_privacy_consent_evidence', ['supersedes_id'], unique=False)
    op.create_index(op.f('ix_security_privacy_consent_evidence_workspace_id'), 'security_privacy_consent_evidence', ['workspace_id'], unique=False)
    op.create_index('uq_security_consent_root', 'security_privacy_consent_evidence', ['workspace_id', 'subject_id', 'purpose'], unique=True, postgresql_where=sa.text('supersedes_id IS NULL'))
    op.create_foreign_key(
        'fk_sec_consent_supersedes',
        'security_privacy_consent_evidence',
        'security_privacy_consent_evidence',
        ['workspace_id', 'supersedes_id'],
        ['workspace_id', 'id'],
        ondelete='RESTRICT',
        deferrable=True,
        initially='DEFERRED',
    )
    op.create_table('security_retention_policy_versions',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('rules', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('data_region', sa.String(length=80), nullable=False),
    sa.Column('cross_border_policy', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('backup_erasure_policy', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('legal_basis_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('policy_hash', sa.String(length=64), nullable=False),
    sa.Column('effective_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('version > 0', name=op.f('ck_security_retention_policy_versions_version_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_retention_policy_versions')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_retention_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'version', name='security_retention_version')
    )
    op.create_index('ix_security_retention_effective', 'security_retention_policy_versions', ['workspace_id', 'effective_at'], unique=False)
    op.create_index(op.f('ix_security_retention_policy_versions_workspace_id'), 'security_retention_policy_versions', ['workspace_id'], unique=False)
    op.create_table('security_subprocessor_versions',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('vendor_key', sa.String(length=120), nullable=False),
    sa.Column('vendor_name', sa.String(length=240), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('purposes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('data_classes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('processing_countries', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('transfer_mechanism', sa.String(length=240), nullable=True),
    sa.Column('retention_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('security_measures', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('contract_artifact_ref', sa.String(length=1000), nullable=False),
    sa.Column('contract_hash', sa.String(length=64), nullable=False),
    sa.Column('notice_required', sa.Boolean(), nullable=False),
    sa.Column('notice_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('effective_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('version > 0', name=op.f('ck_security_subprocessor_versions_version_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_subprocessor_versions')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_subprocessor_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'vendor_key', 'version', name='security_subprocessor_version')
    )
    op.create_index('ix_security_subprocessor_effective', 'security_subprocessor_versions', ['workspace_id', 'effective_at'], unique=False)
    op.create_index(op.f('ix_security_subprocessor_versions_workspace_id'), 'security_subprocessor_versions', ['workspace_id'], unique=False)
    op.create_table('operations_backup_runs',
    sa.Column('policy_version_id', sa.Uuid(), nullable=False),
    sa.Column('policy_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('policy_snapshot_hash', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('requested_by', sa.Uuid(), nullable=False),
    sa.Column('state', sa.String(length=24), nullable=False),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('provider_run_ref', sa.String(length=500), nullable=True),
    sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_code', sa.String(length=120), nullable=True),
    sa.Column('lock_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('attempt_count >= 0', name=op.f('ck_operations_backup_runs_attempt_nonnegative')),
    sa.CheckConstraint('lock_version > 0', name=op.f('ck_operations_backup_runs_lock_positive')),
    sa.ForeignKeyConstraint(['policy_version_id'], ['operations_backup_policy_versions.id'], name='fk_ops_backup_run_policy', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_backup_runs')),
    sa.UniqueConstraint('idempotency_key', name='operations_backup_idempotency')
    )
    op.create_index('ix_operations_backup_run_queue', 'operations_backup_runs', ['state', 'requested_at'], unique=False)
    op.create_index(op.f('ix_operations_backup_runs_policy_version_id'), 'operations_backup_runs', ['policy_version_id'], unique=False)
    op.create_table('operations_ga_gate_evidence',
    sa.Column('assessment_id', sa.Uuid(), nullable=False),
    sa.Column('gate', sa.String(length=64), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('verifier', sa.String(length=160), nullable=False),
    sa.Column('source_artifact_ref', sa.String(length=1000), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reason_codes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['assessment_id'], ['operations_ga_assessments.id'], name='fk_ops_ga_gate_assessment', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_ga_gate_evidence')),
    sa.UniqueConstraint('assessment_id', 'gate', name='operations_ga_gate_once')
    )
    op.create_index('ix_operations_ga_gate_assessment', 'operations_ga_gate_evidence', ['assessment_id', 'gate'], unique=False)
    op.create_index(op.f('ix_operations_ga_gate_evidence_assessment_id'), 'operations_ga_gate_evidence', ['assessment_id'], unique=False)
    op.create_table('operations_health_observations',
    sa.Column('component_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_until', sa.DateTime(timezone=True), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('safe_metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('latency_ms IS NULL OR latency_ms >= 0', name=op.f('ck_operations_health_observations_latency_nonnegative')),
    sa.ForeignKeyConstraint(['component_id'], ['operations_service_components.id'], name='fk_ops_health_component', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_health_observations')),
    sa.UniqueConstraint('component_id', 'checked_at', name='operations_health_check_once')
    )
    op.create_index('ix_operations_health_component', 'operations_health_observations', ['component_id', 'checked_at'], unique=False)
    op.create_index('ix_operations_health_expiry', 'operations_health_observations', ['valid_until'], unique=False)
    op.create_index(op.f('ix_operations_health_observations_component_id'), 'operations_health_observations', ['component_id'], unique=False)
    op.create_table('operations_incidents',
    sa.Column('external_ref', sa.String(length=500), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('safe_summary', sa.Text(), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('state', sa.String(length=24), nullable=False),
    sa.Column('component_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('affected_workspace_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('identified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('runbook_version_id', sa.Uuid(), nullable=False),
    sa.Column('opened_by', sa.Uuid(), nullable=False),
    sa.Column('lock_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('lock_version > 0', name=op.f('ck_operations_incidents_lock_positive')),
    sa.ForeignKeyConstraint(['runbook_version_id'], ['operations_runbook_versions.id'], name='fk_ops_incident_runbook', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_incidents')),
    sa.UniqueConstraint('external_ref', name='operations_incident_external')
    )
    op.create_index('ix_operations_incident_state', 'operations_incidents', ['severity', 'state', 'started_at'], unique=False)
    op.create_index(op.f('ix_operations_incidents_runbook_version_id'), 'operations_incidents', ['runbook_version_id'], unique=False)
    op.create_table('security_breach_notifications',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('audience', sa.String(length=32), nullable=False),
    sa.Column('destination_hash', sa.String(length=64), nullable=False),
    sa.Column('template_version', sa.String(length=80), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.Column('provider_message_ref', sa.String(length=500), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id', 'incident_id'], ['security_incidents.workspace_id', 'security_incidents.id'], name='fk_sec_breach_notice_incident', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_breach_notifications')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_breach_notice_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'incident_id', 'audience', 'destination_hash', 'template_version', 'payload_hash', name='security_breach_notice_destination')
    )
    op.create_index('ix_security_breach_notice_incident', 'security_breach_notifications', ['workspace_id', 'incident_id', 'delivered_at'], unique=False)
    op.create_index(op.f('ix_security_breach_notifications_incident_id'), 'security_breach_notifications', ['incident_id'], unique=False)
    op.create_index(op.f('ix_security_breach_notifications_workspace_id'), 'security_breach_notifications', ['workspace_id'], unique=False)
    op.create_table('security_copyright_case_events',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('case_id', sa.Uuid(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('metadata_safe', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('evidence_object_ref', sa.String(length=1000), nullable=True),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('previous_event_hash', sa.String(length=64), nullable=True),
    sa.Column('event_hash', sa.String(length=64), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('sequence > 0', name=op.f('ck_security_copyright_case_events_sequence_positive')),
    sa.ForeignKeyConstraint(['workspace_id', 'case_id'], ['security_copyright_cases.workspace_id', 'security_copyright_cases.id'], name='fk_sec_copyright_event_case', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_copyright_case_events')),
    sa.UniqueConstraint('workspace_id', 'case_id', 'sequence', name='security_copyright_event_sequence'),
    sa.UniqueConstraint('workspace_id', 'id', name='security_copyright_event_workspace_id')
    )
    op.create_index(op.f('ix_security_copyright_case_events_case_id'), 'security_copyright_case_events', ['case_id'], unique=False)
    op.create_index(op.f('ix_security_copyright_case_events_workspace_id'), 'security_copyright_case_events', ['workspace_id'], unique=False)
    op.create_index('ix_security_copyright_event_case', 'security_copyright_case_events', ['workspace_id', 'case_id', 'sequence'], unique=False)
    op.create_table('security_copyright_counter_notices',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('case_id', sa.Uuid(), nullable=False),
    sa.Column('submitted_by', sa.Uuid(), nullable=False),
    sa.Column('respondent_contact_ref', sa.String(length=1000), nullable=False),
    sa.Column('respondent_contact_hash', sa.String(length=64), nullable=False),
    sa.Column('statement_object_ref', sa.String(length=1000), nullable=False),
    sa.Column('statement_hash', sa.String(length=64), nullable=False),
    sa.Column('sworn_statement', sa.Boolean(), nullable=False),
    sa.Column('verification_reference', sa.String(length=500), nullable=False),
    sa.Column('verification_assurance', sa.String(length=80), nullable=False),
    sa.Column('verification_evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id', 'case_id'], ['security_copyright_cases.workspace_id', 'security_copyright_cases.id'], name='fk_sec_counter_notice_case', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_copyright_counter_notices')),
    sa.UniqueConstraint('workspace_id', 'case_id', name='security_counter_notice_case'),
    sa.UniqueConstraint('workspace_id', 'id', name='security_counter_notice_workspace_id')
    )
    op.create_index(op.f('ix_security_copyright_counter_notices_case_id'), 'security_copyright_counter_notices', ['case_id'], unique=False)
    op.create_index(op.f('ix_security_copyright_counter_notices_workspace_id'), 'security_copyright_counter_notices', ['workspace_id'], unique=False)
    op.create_table('security_incident_events',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('state_after', sa.String(length=24), nullable=False),
    sa.Column('safe_summary', sa.Text(), nullable=False),
    sa.Column('evidence_object_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('previous_event_hash', sa.String(length=64), nullable=True),
    sa.Column('event_hash', sa.String(length=64), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('sequence > 0', name=op.f('ck_security_incident_events_sequence_positive')),
    sa.ForeignKeyConstraint(['workspace_id', 'incident_id'], ['security_incidents.workspace_id', 'security_incidents.id'], name='fk_sec_incident_event_incident', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_incident_events')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_incident_event_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'incident_id', 'sequence', name='security_incident_event_sequence')
    )
    op.create_index('ix_security_incident_event', 'security_incident_events', ['workspace_id', 'incident_id', 'sequence'], unique=False)
    op.create_index(op.f('ix_security_incident_events_incident_id'), 'security_incident_events', ['incident_id'], unique=False)
    op.create_index(op.f('ix_security_incident_events_workspace_id'), 'security_incident_events', ['workspace_id'], unique=False)
    op.create_table('security_legal_hold_events',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('hold_id', sa.Uuid(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('previous_event_hash', sa.String(length=64), nullable=True),
    sa.Column('event_hash', sa.String(length=64), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('sequence > 0', name=op.f('ck_security_legal_hold_events_sequence_positive')),
    sa.ForeignKeyConstraint(['workspace_id', 'hold_id'], ['security_legal_holds.workspace_id', 'security_legal_holds.id'], name='fk_sec_legal_event_hold', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_legal_hold_events')),
    sa.UniqueConstraint('workspace_id', 'hold_id', 'sequence', name='security_legal_sequence'),
    sa.UniqueConstraint('workspace_id', 'id', name='security_legal_event_workspace_id')
    )
    op.create_index('ix_security_legal_event_hold', 'security_legal_hold_events', ['workspace_id', 'hold_id', 'sequence'], unique=False)
    op.create_index(op.f('ix_security_legal_hold_events_hold_id'), 'security_legal_hold_events', ['hold_id'], unique=False)
    op.create_index(op.f('ix_security_legal_hold_events_workspace_id'), 'security_legal_hold_events', ['workspace_id'], unique=False)
    op.create_table('security_privacy_requests',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('requested_by', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('external_request_ref', sa.String(length=500), nullable=True),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('subject_locator_ref', sa.String(length=1000), nullable=False),
    sa.Column('subject_locator_hash', sa.String(length=64), nullable=False),
    sa.Column('data_classes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('requested_correction_ref', sa.String(length=1000), nullable=True),
    sa.Column('requester_relationship', sa.String(length=40), nullable=False),
    sa.Column('retention_policy_version_id', sa.Uuid(), nullable=False),
    sa.Column('retention_policy_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rejection_code', sa.String(length=120), nullable=True),
    sa.Column('failure_code', sa.String(length=120), nullable=True),
    sa.Column('lock_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('lock_version > 0', name=op.f('ck_security_privacy_requests_lock_positive')),
    sa.ForeignKeyConstraint(['workspace_id', 'retention_policy_version_id'], ['security_retention_policy_versions.workspace_id', 'security_retention_policy_versions.id'], name='fk_sec_priv_request_policy', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_privacy_requests')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_privacy_request_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'requested_by', 'kind', 'idempotency_key', name='security_privacy_request_idempotency'),
    sa.UniqueConstraint('workspace_id', 'source', 'external_request_ref', name='security_privacy_request_external')
    )
    op.create_index('ix_security_privacy_request_sla', 'security_privacy_requests', ['workspace_id', 'state', 'due_at'], unique=False)
    op.create_index(op.f('ix_security_privacy_requests_requested_by'), 'security_privacy_requests', ['requested_by'], unique=False)
    op.create_index(op.f('ix_security_privacy_requests_subject_locator_hash'), 'security_privacy_requests', ['subject_locator_hash'], unique=False)
    op.create_index(op.f('ix_security_privacy_requests_workspace_id'), 'security_privacy_requests', ['workspace_id'], unique=False)
    op.create_table('security_retention_sweeps',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('policy_version_id', sa.Uuid(), nullable=False),
    sa.Column('policy_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('policy_snapshot_hash', sa.String(length=64), nullable=False),
    sa.Column('legal_hold_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('legal_hold_snapshot_hash', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('requested_by', sa.Uuid(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_code', sa.String(length=120), nullable=True),
    sa.Column('lock_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('lock_version > 0', name=op.f('ck_security_retention_sweeps_lock_positive')),
    sa.ForeignKeyConstraint(['workspace_id', 'policy_version_id'], ['security_retention_policy_versions.workspace_id', 'security_retention_policy_versions.id'], name='fk_sec_ret_sweep_policy', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_retention_sweeps')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_retention_sweep_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'idempotency_key', name='security_retention_sweep_idempotency')
    )
    op.create_index('ix_security_retention_sweep_queue', 'security_retention_sweeps', ['workspace_id', 'state', 'created_at'], unique=False)
    op.create_index(op.f('ix_security_retention_sweeps_policy_version_id'), 'security_retention_sweeps', ['policy_version_id'], unique=False)
    op.create_index(op.f('ix_security_retention_sweeps_workspace_id'), 'security_retention_sweeps', ['workspace_id'], unique=False)
    op.create_table('operations_backup_evidence',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('provider_run_ref', sa.String(length=500), nullable=False),
    sa.Column('snapshot_ref', sa.String(length=1000), nullable=False),
    sa.Column('snapshot_hash', sa.String(length=64), nullable=False),
    sa.Column('encryption_key_version', sa.String(length=80), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('restore_point_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('evidence_object_ref', sa.String(length=1000), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_operations_backup_evidence_size_nonnegative')),
    sa.ForeignKeyConstraint(['run_id'], ['operations_backup_runs.id'], name='fk_ops_backup_evidence_run', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_backup_evidence')),
    sa.UniqueConstraint('run_id', name='operations_backup_evidence_run')
    )
    op.create_index(op.f('ix_operations_backup_evidence_run_id'), 'operations_backup_evidence', ['run_id'], unique=False)
    op.create_index('ix_operations_backup_restore_point', 'operations_backup_evidence', ['restore_point_at'], unique=False)
    op.create_table('operations_incident_events',
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('state_after', sa.String(length=24), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('safe_summary', sa.Text(), nullable=False),
    sa.Column('evidence_object_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('previous_event_hash', sa.String(length=64), nullable=True),
    sa.Column('event_hash', sa.String(length=64), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('sequence > 0', name=op.f('ck_operations_incident_events_sequence_positive')),
    sa.ForeignKeyConstraint(['incident_id'], ['operations_incidents.id'], name='fk_ops_incident_event_incident', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_incident_events')),
    sa.UniqueConstraint('incident_id', 'sequence', name='operations_incident_event_sequence')
    )
    op.create_index('ix_operations_incident_event', 'operations_incident_events', ['incident_id', 'sequence'], unique=False)
    op.create_index(op.f('ix_operations_incident_events_incident_id'), 'operations_incident_events', ['incident_id'], unique=False)
    op.create_table('operations_status_notification_evidence',
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('audience', sa.String(length=80), nullable=False),
    sa.Column('template_version', sa.String(length=80), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.Column('provider_message_ref', sa.String(length=500), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['operations_incidents.id'], name='fk_ops_status_notice_incident', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_status_notification_evidence')),
    sa.UniqueConstraint('incident_id', 'audience', 'template_version', 'payload_hash', name='operations_status_notice_once')
    )
    op.create_index('ix_operations_status_notice', 'operations_status_notification_evidence', ['incident_id', 'delivered_at'], unique=False)
    op.create_index(op.f('ix_operations_status_notification_evidence_incident_id'), 'operations_status_notification_evidence', ['incident_id'], unique=False)
    op.create_table('security_deletion_certificates',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('request_id', sa.Uuid(), nullable=False),
    sa.Column('completed_data_classes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('held_data_classes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('system_results', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('manifest_hash', sa.String(length=64), nullable=False),
    sa.Column('backup_erasure_due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('certificate_code', sa.String(length=120), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id', 'request_id'], ['security_privacy_requests.workspace_id', 'security_privacy_requests.id'], name='fk_sec_delete_cert_request', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_deletion_certificates')),
    sa.UniqueConstraint('certificate_code', name=op.f('uq_security_deletion_certificates_certificate_code')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_deletion_cert_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'request_id', name='security_deletion_cert_request')
    )
    op.create_index(op.f('ix_security_deletion_certificates_request_id'), 'security_deletion_certificates', ['request_id'], unique=False)
    op.create_index(op.f('ix_security_deletion_certificates_workspace_id'), 'security_deletion_certificates', ['workspace_id'], unique=False)
    op.create_table('security_privacy_actions',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('request_id', sa.Uuid(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('data_classes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('target_system', sa.String(length=120), nullable=False),
    sa.Column('target_locator_ref', sa.String(length=1000), nullable=False),
    sa.Column('plan_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('plan_hash', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('provider_operation_ref', sa.String(length=500), nullable=True),
    sa.Column('affected_records', sa.Integer(), nullable=True),
    sa.Column('result_manifest_hash', sa.String(length=64), nullable=True),
    sa.Column('evidence_object_ref', sa.String(length=1000), nullable=True),
    sa.Column('backup_erasure_due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error_code', sa.String(length=120), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lock_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('attempt_count >= 0', name=op.f('ck_security_privacy_actions_attempt_nonnegative')),
    sa.CheckConstraint('lock_version > 0', name=op.f('ck_security_privacy_actions_lock_positive')),
    sa.CheckConstraint('sequence > 0', name=op.f('ck_security_privacy_actions_sequence_positive')),
    sa.ForeignKeyConstraint(['workspace_id', 'request_id'], ['security_privacy_requests.workspace_id', 'security_privacy_requests.id'], name='fk_sec_priv_action_request', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_privacy_actions')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_privacy_action_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'request_id', 'sequence', name='security_privacy_action_sequence')
    )
    op.create_index('ix_security_privacy_action_queue', 'security_privacy_actions', ['workspace_id', 'state', 'sequence'], unique=False)
    op.create_index(op.f('ix_security_privacy_actions_request_id'), 'security_privacy_actions', ['request_id'], unique=False)
    op.create_index(op.f('ix_security_privacy_actions_workspace_id'), 'security_privacy_actions', ['workspace_id'], unique=False)
    op.create_table('security_privacy_export_artifacts',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('request_id', sa.Uuid(), nullable=False),
    sa.Column('object_ref', sa.String(length=1000), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('manifest_hash', sa.String(length=64), nullable=False),
    sa.Column('watermark_policy_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('maximum_downloads', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('maximum_downloads > 0', name=op.f('ck_security_privacy_export_artifacts_downloads_positive')),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_security_privacy_export_artifacts_size_nonnegative')),
    sa.ForeignKeyConstraint(['workspace_id', 'request_id'], ['security_privacy_requests.workspace_id', 'security_privacy_requests.id'], name='fk_sec_priv_export_request', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_privacy_export_artifacts')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_privacy_export_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'request_id', name='security_privacy_export_request')
    )
    op.create_index(op.f('ix_security_privacy_export_artifacts_request_id'), 'security_privacy_export_artifacts', ['request_id'], unique=False)
    op.create_index(op.f('ix_security_privacy_export_artifacts_workspace_id'), 'security_privacy_export_artifacts', ['workspace_id'], unique=False)
    op.create_index('ix_security_privacy_export_expiry', 'security_privacy_export_artifacts', ['workspace_id', 'expires_at'], unique=False)
    op.create_table('security_privacy_verification_events',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('request_id', sa.Uuid(), nullable=False),
    sa.Column('outcome', sa.String(length=16), nullable=False),
    sa.Column('provider_reference', sa.String(length=500), nullable=False),
    sa.Column('assurance_level', sa.String(length=80), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('failure_code', sa.String(length=120), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id', 'request_id'], ['security_privacy_requests.workspace_id', 'security_privacy_requests.id'], name='fk_sec_priv_verify_request', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_privacy_verification_events')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_privacy_verify_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'provider_reference', name='security_privacy_verify_provider')
    )
    op.create_index(op.f('ix_security_privacy_verification_events_request_id'), 'security_privacy_verification_events', ['request_id'], unique=False)
    op.create_index(op.f('ix_security_privacy_verification_events_workspace_id'), 'security_privacy_verification_events', ['workspace_id'], unique=False)
    op.create_index('ix_security_privacy_verify_request', 'security_privacy_verification_events', ['workspace_id', 'request_id', 'verified_at'], unique=False)
    op.create_table('security_provider_deletion_events',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('privacy_request_id', sa.Uuid(), nullable=False),
    sa.Column('provider', sa.String(length=80), nullable=False),
    sa.Column('provider_event_id', sa.String(length=500), nullable=False),
    sa.Column('raw_payload_hash', sa.String(length=64), nullable=False),
    sa.Column('signature_key_version', sa.String(length=80), nullable=False),
    sa.Column('subject_locator_hash', sa.String(length=64), nullable=False),
    sa.Column('data_classes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('assurance_level', sa.String(length=80), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id', 'privacy_request_id'], ['security_privacy_requests.workspace_id', 'security_privacy_requests.id'], name='fk_sec_provider_delete_request', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_provider_deletion_events')),
    sa.UniqueConstraint('provider', 'provider_event_id', name='security_provider_delete_once'),
    sa.UniqueConstraint('workspace_id', 'id', name='security_provider_delete_workspace_id')
    )
    op.create_index('ix_security_provider_delete_workspace', 'security_provider_deletion_events', ['workspace_id', 'occurred_at'], unique=False)
    op.create_index(op.f('ix_security_provider_deletion_events_privacy_request_id'), 'security_provider_deletion_events', ['privacy_request_id'], unique=False)
    op.create_index(op.f('ix_security_provider_deletion_events_workspace_id'), 'security_provider_deletion_events', ['workspace_id'], unique=False)
    op.create_table('security_retention_disposition_evidence',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('sweep_id', sa.Uuid(), nullable=False),
    sa.Column('data_class', sa.String(length=40), nullable=False),
    sa.Column('target_system', sa.String(length=120), nullable=False),
    sa.Column('cutoff_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('disposition', sa.String(length=24), nullable=False),
    sa.Column('affected_records', sa.Integer(), nullable=False),
    sa.Column('held_records', sa.Integer(), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('evidence_object_ref', sa.String(length=1000), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('affected_records >= 0', name=op.f('ck_security_retention_disposition_evidence_affected_nonnegative')),
    sa.CheckConstraint('held_records >= 0', name=op.f('ck_security_retention_disposition_evidence_held_nonnegative')),
    sa.ForeignKeyConstraint(['workspace_id', 'sweep_id'], ['security_retention_sweeps.workspace_id', 'security_retention_sweeps.id'], name='fk_sec_ret_evidence_sweep', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_retention_disposition_evidence')),
    sa.UniqueConstraint('workspace_id', 'id', name='security_retention_result_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'sweep_id', 'data_class', 'target_system', name='security_retention_result_target')
    )
    op.create_index(op.f('ix_security_retention_disposition_evidence_sweep_id'), 'security_retention_disposition_evidence', ['sweep_id'], unique=False)
    op.create_index(op.f('ix_security_retention_disposition_evidence_workspace_id'), 'security_retention_disposition_evidence', ['workspace_id'], unique=False)
    op.create_index('ix_security_retention_result_sweep', 'security_retention_disposition_evidence', ['workspace_id', 'sweep_id'], unique=False)
    op.create_table('operations_recovery_exercises',
    sa.Column('backup_evidence_id', sa.Uuid(), nullable=False),
    sa.Column('runbook_version_id', sa.Uuid(), nullable=False),
    sa.Column('rpo_minutes', sa.Integer(), nullable=False),
    sa.Column('rto_minutes', sa.Integer(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('requested_by', sa.Uuid(), nullable=False),
    sa.Column('state', sa.String(length=24), nullable=False),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('provider_run_ref', sa.String(length=500), nullable=True),
    sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_code', sa.String(length=120), nullable=True),
    sa.Column('lock_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('attempt_count >= 0', name=op.f('ck_operations_recovery_exercises_attempt_nonnegative')),
    sa.CheckConstraint('lock_version > 0', name=op.f('ck_operations_recovery_exercises_lock_positive')),
    sa.ForeignKeyConstraint(['backup_evidence_id'], ['operations_backup_evidence.id'], name='fk_ops_recovery_backup_evidence', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['runbook_version_id'], ['operations_runbook_versions.id'], name='fk_ops_recovery_runbook', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_recovery_exercises')),
    sa.UniqueConstraint('idempotency_key', name='operations_recovery_idempotency')
    )
    op.create_index(op.f('ix_operations_recovery_exercises_backup_evidence_id'), 'operations_recovery_exercises', ['backup_evidence_id'], unique=False)
    op.create_index(op.f('ix_operations_recovery_exercises_runbook_version_id'), 'operations_recovery_exercises', ['runbook_version_id'], unique=False)
    op.create_index('ix_operations_recovery_queue', 'operations_recovery_exercises', ['state', 'requested_at'], unique=False)
    op.create_table('security_backup_erasure_evidence',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('certificate_id', sa.Uuid(), nullable=False),
    sa.Column('provider_reference', sa.String(length=500), nullable=False),
    sa.Column('verifier', sa.String(length=160), nullable=False),
    sa.Column('evidence_object_ref', sa.String(length=1000), nullable=False),
    sa.Column('submitted_evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('verified_evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id', 'certificate_id'], ['security_deletion_certificates.workspace_id', 'security_deletion_certificates.id'], name='fk_sec_backup_erase_cert', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_backup_erasure_evidence')),
    sa.UniqueConstraint('workspace_id', 'certificate_id', name='security_backup_erasure_certificate'),
    sa.UniqueConstraint('workspace_id', 'id', name='security_backup_erasure_workspace_id'),
    sa.UniqueConstraint('workspace_id', 'provider_reference', name='security_backup_erasure_provider')
    )
    op.create_index(op.f('ix_security_backup_erasure_evidence_certificate_id'), 'security_backup_erasure_evidence', ['certificate_id'], unique=False)
    op.create_index(op.f('ix_security_backup_erasure_evidence_workspace_id'), 'security_backup_erasure_evidence', ['workspace_id'], unique=False)
    op.create_table('security_privacy_action_attempts',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('action_id', sa.Uuid(), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('outcome', sa.String(length=24), nullable=False),
    sa.Column('provider_operation_ref', sa.String(length=500), nullable=True),
    sa.Column('result_manifest_hash', sa.String(length=64), nullable=True),
    sa.Column('evidence_object_ref', sa.String(length=1000), nullable=True),
    sa.Column('error_code', sa.String(length=120), nullable=True),
    sa.Column('error_class', sa.String(length=120), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('attempt_no > 0', name=op.f('ck_security_privacy_action_attempts_attempt_positive')),
    sa.ForeignKeyConstraint(['workspace_id', 'action_id'], ['security_privacy_actions.workspace_id', 'security_privacy_actions.id'], name='fk_sec_priv_attempt_action', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_privacy_action_attempts')),
    sa.UniqueConstraint('workspace_id', 'action_id', 'attempt_no', name='security_privacy_action_attempt'),
    sa.UniqueConstraint('workspace_id', 'id', name='security_privacy_attempt_workspace_id')
    )
    op.create_index(op.f('ix_security_privacy_action_attempts_action_id'), 'security_privacy_action_attempts', ['action_id'], unique=False)
    op.create_index(op.f('ix_security_privacy_action_attempts_workspace_id'), 'security_privacy_action_attempts', ['workspace_id'], unique=False)
    op.create_index('ix_security_privacy_attempt_action', 'security_privacy_action_attempts', ['workspace_id', 'action_id', 'attempt_no'], unique=False)
    op.create_table('operations_recovery_evidence',
    sa.Column('exercise_id', sa.Uuid(), nullable=False),
    sa.Column('provider_run_ref', sa.String(length=500), nullable=False),
    sa.Column('isolated_environment_ref', sa.String(length=1000), nullable=False),
    sa.Column('data_loss_minutes', sa.Integer(), nullable=False),
    sa.Column('recovery_minutes', sa.Integer(), nullable=False),
    sa.Column('objectives_met', sa.Boolean(), nullable=False),
    sa.Column('integrity_checks', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('evidence_object_ref', sa.String(length=1000), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('data_loss_minutes >= 0', name=op.f('ck_operations_recovery_evidence_loss_nonnegative')),
    sa.CheckConstraint('recovery_minutes >= 0', name=op.f('ck_operations_recovery_evidence_time_nonnegative')),
    sa.ForeignKeyConstraint(['exercise_id'], ['operations_recovery_exercises.id'], name='fk_ops_recovery_evidence_exercise', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations_recovery_evidence')),
    sa.UniqueConstraint('exercise_id', name='operations_recovery_evidence_exercise')
    )
    op.create_index(op.f('ix_operations_recovery_evidence_exercise_id'), 'operations_recovery_evidence', ['exercise_id'], unique=False)

    # Only the built-in privileged roles receive the Stage 9 security permissions.
    # This is intentionally retained on downgrade so rollback cannot weaken an
    # existing system role or alter any user-defined role.
    op.execute(
        '''
        UPDATE public.roles AS role
        SET permissions = (
            SELECT jsonb_agg(permission_entry.permission ORDER BY permission_entry.permission)
            FROM (
                SELECT DISTINCT permission.value AS permission
                FROM jsonb_array_elements_text(
                    (
                        CASE
                            WHEN jsonb_typeof(role.permissions) = 'array'
                                THEN role.permissions
                            ELSE '[]'::jsonb
                        END
                    ) || '["privacy:read", "privacy:manage", "security:read", "security:manage"]'::jsonb
                ) AS permission(value)
            ) AS permission_entry
        )
        WHERE role.is_system IS TRUE
          AND role.key IN ('owner', 'admin')
        '''
    )

    # Existing workspaces must enforce MFA for both privileged tenant roles. This
    # data strengthening is intentionally retained on downgrade.
    op.execute(
        '''
        UPDATE public.workspace_authentication_policies AS policy
        SET require_mfa_role_keys = (
            SELECT jsonb_agg(required_role.role_key ORDER BY required_role.role_key)
            FROM (
                SELECT DISTINCT role.role_key
                FROM jsonb_array_elements_text(
                    (
                        CASE
                            WHEN jsonb_typeof(policy.require_mfa_role_keys) = 'array'
                                THEN policy.require_mfa_role_keys
                            ELSE '[]'::jsonb
                        END
                    )
                    || '["owner", "admin"]'::jsonb
                ) AS role(role_key)
            ) AS required_role
        )
        WHERE NOT (
            COALESCE(policy.require_mfa_role_keys, '[]'::jsonb)
            @> '["owner", "admin"]'::jsonb
        )
        '''
    )
    op.create_check_constraint(
        op.f('ck_workspace_authentication_policies_auth_policy_privileged_mfa'),
        'workspace_authentication_policies',
        "jsonb_typeof(require_mfa_role_keys) = 'array' "
        "AND require_mfa_role_keys @> '[\"owner\", \"admin\"]'::jsonb",
    )

    for table_name in SECURITY_TABLES:
        op.execute(
            f'ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY'
        )
        op.execute(
            f'ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY'
        )
        op.execute(
            f'''
            CREATE POLICY {table_name}_workspace_isolation
            ON public.{table_name}
            USING (workspace_id = app.current_workspace_id())
            WITH CHECK (workspace_id = app.current_workspace_id())
            '''
        )

    for table_name in OPERATIONS_TABLES:
        op.execute(
            f'ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY'
        )
        op.execute(
            f'ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY'
        )
        if table_name in OPERATIONS_WORKER_TABLES:
            policy_name = f'{table_name}_platform_worker'
            predicate = (
                "app.current_platform_operator_id() IS NOT NULL "
                "OR current_user = 'blogops_worker'"
            )
        else:
            policy_name = f'{table_name}_platform'
            predicate = 'app.current_platform_operator_id() IS NOT NULL'
        op.execute(
            f'''
            CREATE POLICY {policy_name}
            ON public.{table_name}
            USING ({predicate})
            WITH CHECK ({predicate})
            '''
        )

    op.execute(
        '''
        CREATE POLICY audit_logs_stage9_platform
        ON public.audit_logs
        FOR INSERT
        WITH CHECK (
            workspace_id IS NULL
            AND (
                app.current_platform_operator_id() IS NOT NULL
                OR current_user = 'blogops_worker'
            )
        )
        '''
    )

    for table_name in IMMUTABLE_TABLES:
        op.execute(
            f'''
            CREATE TRIGGER {table_name}_immutable
            BEFORE UPDATE OR DELETE ON public.{table_name}
            FOR EACH ROW EXECUTE FUNCTION app.reject_snapshot_update()
            '''
        )

    op.execute(
        '''
        CREATE OR REPLACE FUNCTION app.reject_stage9_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $$
        DECLARE
            frozen_column text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION '% rows cannot be deleted', TG_TABLE_NAME
                    USING ERRCODE = '55000';
            END IF;

            FOREACH frozen_column IN ARRAY TG_ARGV LOOP
                IF (to_jsonb(OLD) -> frozen_column) IS DISTINCT FROM
                   (to_jsonb(NEW) -> frozen_column) THEN
                    RAISE EXCEPTION 'column %.% is immutable',
                        TG_TABLE_NAME, frozen_column
                        USING ERRCODE = '55000';
                END IF;
            END LOOP;
            RETURN NEW;
        END;
        $$
        '''
    )
    for table_name, frozen_fields in HISTORY_FROZEN_FIELDS.items():
        trigger_arguments = ', '.join(
            f"'{field_name}'" for field_name in frozen_fields
        )
        op.execute(
            f'''
            CREATE TRIGGER {table_name}_history_guard
            BEFORE UPDATE OR DELETE ON public.{table_name}
            FOR EACH ROW EXECUTE FUNCTION
                app.reject_stage9_history_mutation({trigger_arguments})
            '''
        )

    op.execute(
        '''
        CREATE OR REPLACE FUNCTION app.public_operations_status()
        RETURNS TABLE (
            component_key text,
            display_name text,
            status text,
            checked_at timestamptz,
            valid_until timestamptz
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        SET row_security = off
        AS $$
            SELECT
                component.component_key::text,
                component.display_name::text,
                COALESCE(observation.status::text, 'UNKNOWN'::text),
                observation.checked_at,
                observation.valid_until
            FROM public.operations_service_components AS component
            LEFT JOIN LATERAL (
                SELECT
                    observed.status,
                    observed.checked_at,
                    observed.valid_until,
                    observed.id
                FROM public.operations_health_observations AS observed
                WHERE observed.component_id = component.id
                  AND observed.valid_until > statement_timestamp()
                ORDER BY observed.checked_at DESC, observed.id DESC
                LIMIT 1
            ) AS observation ON true
            WHERE component.public IS TRUE
              AND component.enabled IS TRUE
            ORDER BY component.component_key
        $$
        '''
    )

    op.execute(
        f'''
        REVOKE ALL PRIVILEGES
            ON TABLE {_qualified_tables(STAGE9_TABLES)}
            FROM PUBLIC
        '''
    )
    op.execute(
        'REVOKE ALL ON FUNCTION app.public_operations_status() FROM PUBLIC'
    )
    op.execute(
        'REVOKE ALL ON FUNCTION app.reject_stage9_history_mutation() FROM PUBLIC'
    )
    op.execute(
        f'''
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'blogops_app') THEN
                GRANT USAGE ON SCHEMA public, app TO blogops_app;
                GRANT SELECT, INSERT, UPDATE
                    ON {_qualified_tables(SECURITY_MUTABLE_TABLES)}
                    TO blogops_app;
                GRANT SELECT, INSERT
                    ON {_qualified_tables(SECURITY_IMMUTABLE_TABLES)}
                    TO blogops_app;
                GRANT SELECT, INSERT, UPDATE
                    ON {_qualified_tables(OPERATIONS_MUTABLE_TABLES)}
                    TO blogops_app;
                GRANT SELECT, INSERT
                    ON {_qualified_tables(OPERATIONS_IMMUTABLE_TABLES)}
                    TO blogops_app;
                GRANT EXECUTE ON FUNCTION app.public_operations_status()
                    TO blogops_app;
            END IF;

            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'blogops_worker') THEN
                GRANT USAGE ON SCHEMA public, app TO blogops_worker;

                GRANT SELECT ON
                    public.security_retention_policy_versions,
                    public.security_legal_holds
                    TO blogops_worker;
                GRANT SELECT, UPDATE ON
                    public.security_retention_sweeps,
                    public.security_privacy_requests,
                    public.security_copyright_cases
                    TO blogops_worker;
                GRANT SELECT, INSERT, UPDATE ON
                    public.security_privacy_actions
                    TO blogops_worker;
                GRANT SELECT, INSERT ON
                    public.security_retention_disposition_evidence,
                    public.security_privacy_action_attempts,
                    public.security_privacy_export_artifacts,
                    public.security_deletion_certificates,
                    public.security_copyright_case_events
                    TO blogops_worker;

                GRANT SELECT ON
                    public.operations_runbook_versions
                    TO blogops_worker;
                GRANT SELECT, UPDATE ON
                    public.operations_backup_runs,
                    public.operations_recovery_exercises,
                    public.operations_ga_assessments
                    TO blogops_worker;
                GRANT SELECT, INSERT ON
                    public.operations_backup_evidence,
                    public.operations_recovery_evidence,
                    public.operations_ga_gate_evidence
                    TO blogops_worker;
            END IF;
        END
        $$
        '''
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(
        op.f('ck_workspace_authentication_policies_auth_policy_privileged_mfa'),
        'workspace_authentication_policies',
        type_='check',
    )
    op.execute(
        'DROP POLICY IF EXISTS audit_logs_stage9_platform ON public.audit_logs'
    )
    op.execute('DROP FUNCTION IF EXISTS app.public_operations_status()')

    for table_name in HISTORY_FROZEN_FIELDS:
        op.execute(
            f'DROP TRIGGER IF EXISTS {table_name}_history_guard '
            f'ON public.{table_name}'
        )
    op.execute(
        'DROP FUNCTION IF EXISTS app.reject_stage9_history_mutation()'
    )

    for table_name in SECURITY_TABLES:
        op.execute(
            f'DROP POLICY IF EXISTS {table_name}_workspace_isolation '
            f'ON public.{table_name}'
        )
    for table_name in OPERATIONS_TABLES:
        policy_suffix = (
            'platform_worker'
            if table_name in OPERATIONS_WORKER_TABLES
            else 'platform'
        )
        op.execute(
            f'DROP POLICY IF EXISTS {table_name}_{policy_suffix} '
            f'ON public.{table_name}'
        )

    op.drop_constraint(
        'fk_sec_consent_supersedes',
        'security_privacy_consent_evidence',
        type_='foreignkey',
    )
    op.drop_index(op.f('ix_operations_recovery_evidence_exercise_id'), table_name='operations_recovery_evidence')
    op.drop_table('operations_recovery_evidence')
    op.drop_index('ix_security_privacy_attempt_action', table_name='security_privacy_action_attempts')
    op.drop_index(op.f('ix_security_privacy_action_attempts_workspace_id'), table_name='security_privacy_action_attempts')
    op.drop_index(op.f('ix_security_privacy_action_attempts_action_id'), table_name='security_privacy_action_attempts')
    op.drop_table('security_privacy_action_attempts')
    op.drop_index(op.f('ix_security_backup_erasure_evidence_workspace_id'), table_name='security_backup_erasure_evidence')
    op.drop_index(op.f('ix_security_backup_erasure_evidence_certificate_id'), table_name='security_backup_erasure_evidence')
    op.drop_table('security_backup_erasure_evidence')
    op.drop_index('ix_operations_recovery_queue', table_name='operations_recovery_exercises')
    op.drop_index(op.f('ix_operations_recovery_exercises_runbook_version_id'), table_name='operations_recovery_exercises')
    op.drop_index(op.f('ix_operations_recovery_exercises_backup_evidence_id'), table_name='operations_recovery_exercises')
    op.drop_table('operations_recovery_exercises')
    op.drop_index('ix_security_retention_result_sweep', table_name='security_retention_disposition_evidence')
    op.drop_index(op.f('ix_security_retention_disposition_evidence_workspace_id'), table_name='security_retention_disposition_evidence')
    op.drop_index(op.f('ix_security_retention_disposition_evidence_sweep_id'), table_name='security_retention_disposition_evidence')
    op.drop_table('security_retention_disposition_evidence')
    op.drop_index(op.f('ix_security_provider_deletion_events_workspace_id'), table_name='security_provider_deletion_events')
    op.drop_index(op.f('ix_security_provider_deletion_events_privacy_request_id'), table_name='security_provider_deletion_events')
    op.drop_index('ix_security_provider_delete_workspace', table_name='security_provider_deletion_events')
    op.drop_table('security_provider_deletion_events')
    op.drop_index('ix_security_privacy_verify_request', table_name='security_privacy_verification_events')
    op.drop_index(op.f('ix_security_privacy_verification_events_workspace_id'), table_name='security_privacy_verification_events')
    op.drop_index(op.f('ix_security_privacy_verification_events_request_id'), table_name='security_privacy_verification_events')
    op.drop_table('security_privacy_verification_events')
    op.drop_index('ix_security_privacy_export_expiry', table_name='security_privacy_export_artifacts')
    op.drop_index(op.f('ix_security_privacy_export_artifacts_workspace_id'), table_name='security_privacy_export_artifacts')
    op.drop_index(op.f('ix_security_privacy_export_artifacts_request_id'), table_name='security_privacy_export_artifacts')
    op.drop_table('security_privacy_export_artifacts')
    op.drop_index(op.f('ix_security_privacy_actions_workspace_id'), table_name='security_privacy_actions')
    op.drop_index(op.f('ix_security_privacy_actions_request_id'), table_name='security_privacy_actions')
    op.drop_index('ix_security_privacy_action_queue', table_name='security_privacy_actions')
    op.drop_table('security_privacy_actions')
    op.drop_index(op.f('ix_security_deletion_certificates_workspace_id'), table_name='security_deletion_certificates')
    op.drop_index(op.f('ix_security_deletion_certificates_request_id'), table_name='security_deletion_certificates')
    op.drop_table('security_deletion_certificates')
    op.drop_index(op.f('ix_operations_status_notification_evidence_incident_id'), table_name='operations_status_notification_evidence')
    op.drop_index('ix_operations_status_notice', table_name='operations_status_notification_evidence')
    op.drop_table('operations_status_notification_evidence')
    op.drop_index(op.f('ix_operations_incident_events_incident_id'), table_name='operations_incident_events')
    op.drop_index('ix_operations_incident_event', table_name='operations_incident_events')
    op.drop_table('operations_incident_events')
    op.drop_index('ix_operations_backup_restore_point', table_name='operations_backup_evidence')
    op.drop_index(op.f('ix_operations_backup_evidence_run_id'), table_name='operations_backup_evidence')
    op.drop_table('operations_backup_evidence')
    op.drop_index(op.f('ix_security_retention_sweeps_workspace_id'), table_name='security_retention_sweeps')
    op.drop_index(op.f('ix_security_retention_sweeps_policy_version_id'), table_name='security_retention_sweeps')
    op.drop_index('ix_security_retention_sweep_queue', table_name='security_retention_sweeps')
    op.drop_table('security_retention_sweeps')
    op.drop_index(op.f('ix_security_privacy_requests_workspace_id'), table_name='security_privacy_requests')
    op.drop_index(op.f('ix_security_privacy_requests_subject_locator_hash'), table_name='security_privacy_requests')
    op.drop_index(op.f('ix_security_privacy_requests_requested_by'), table_name='security_privacy_requests')
    op.drop_index('ix_security_privacy_request_sla', table_name='security_privacy_requests')
    op.drop_table('security_privacy_requests')
    op.drop_index(op.f('ix_security_legal_hold_events_workspace_id'), table_name='security_legal_hold_events')
    op.drop_index(op.f('ix_security_legal_hold_events_hold_id'), table_name='security_legal_hold_events')
    op.drop_index('ix_security_legal_event_hold', table_name='security_legal_hold_events')
    op.drop_table('security_legal_hold_events')
    op.drop_index(op.f('ix_security_incident_events_workspace_id'), table_name='security_incident_events')
    op.drop_index(op.f('ix_security_incident_events_incident_id'), table_name='security_incident_events')
    op.drop_index('ix_security_incident_event', table_name='security_incident_events')
    op.drop_table('security_incident_events')
    op.drop_index(op.f('ix_security_copyright_counter_notices_workspace_id'), table_name='security_copyright_counter_notices')
    op.drop_index(op.f('ix_security_copyright_counter_notices_case_id'), table_name='security_copyright_counter_notices')
    op.drop_table('security_copyright_counter_notices')
    op.drop_index('ix_security_copyright_event_case', table_name='security_copyright_case_events')
    op.drop_index(op.f('ix_security_copyright_case_events_workspace_id'), table_name='security_copyright_case_events')
    op.drop_index(op.f('ix_security_copyright_case_events_case_id'), table_name='security_copyright_case_events')
    op.drop_table('security_copyright_case_events')
    op.drop_index(op.f('ix_security_breach_notifications_workspace_id'), table_name='security_breach_notifications')
    op.drop_index(op.f('ix_security_breach_notifications_incident_id'), table_name='security_breach_notifications')
    op.drop_index('ix_security_breach_notice_incident', table_name='security_breach_notifications')
    op.drop_table('security_breach_notifications')
    op.drop_index(op.f('ix_operations_incidents_runbook_version_id'), table_name='operations_incidents')
    op.drop_index('ix_operations_incident_state', table_name='operations_incidents')
    op.drop_table('operations_incidents')
    op.drop_index(op.f('ix_operations_health_observations_component_id'), table_name='operations_health_observations')
    op.drop_index('ix_operations_health_expiry', table_name='operations_health_observations')
    op.drop_index('ix_operations_health_component', table_name='operations_health_observations')
    op.drop_table('operations_health_observations')
    op.drop_index(op.f('ix_operations_ga_gate_evidence_assessment_id'), table_name='operations_ga_gate_evidence')
    op.drop_index('ix_operations_ga_gate_assessment', table_name='operations_ga_gate_evidence')
    op.drop_table('operations_ga_gate_evidence')
    op.drop_index(op.f('ix_operations_backup_runs_policy_version_id'), table_name='operations_backup_runs')
    op.drop_index('ix_operations_backup_run_queue', table_name='operations_backup_runs')
    op.drop_table('operations_backup_runs')
    op.drop_index(op.f('ix_security_subprocessor_versions_workspace_id'), table_name='security_subprocessor_versions')
    op.drop_index('ix_security_subprocessor_effective', table_name='security_subprocessor_versions')
    op.drop_table('security_subprocessor_versions')
    op.drop_index(op.f('ix_security_retention_policy_versions_workspace_id'), table_name='security_retention_policy_versions')
    op.drop_index('ix_security_retention_effective', table_name='security_retention_policy_versions')
    op.drop_table('security_retention_policy_versions')
    op.drop_index('uq_security_consent_root', table_name='security_privacy_consent_evidence', postgresql_where=sa.text('supersedes_id IS NULL'))
    op.drop_index(op.f('ix_security_privacy_consent_evidence_workspace_id'), table_name='security_privacy_consent_evidence')
    op.drop_index(op.f('ix_security_privacy_consent_evidence_supersedes_id'), table_name='security_privacy_consent_evidence')
    op.drop_index(op.f('ix_security_privacy_consent_evidence_subject_id'), table_name='security_privacy_consent_evidence')
    op.drop_index('ix_security_consent_subject', table_name='security_privacy_consent_evidence')
    op.drop_table('security_privacy_consent_evidence')
    op.drop_index('ix_security_privacy_access_time', table_name='security_privacy_access_events')
    op.drop_index('ix_security_privacy_access_subject', table_name='security_privacy_access_events')
    op.drop_index(op.f('ix_security_privacy_access_events_workspace_id'), table_name='security_privacy_access_events')
    op.drop_index(op.f('ix_security_privacy_access_events_actor_id'), table_name='security_privacy_access_events')
    op.drop_table('security_privacy_access_events')
    op.drop_index(op.f('ix_security_legal_holds_workspace_id'), table_name='security_legal_holds')
    op.drop_index('ix_security_legal_hold_active', table_name='security_legal_holds')
    op.drop_table('security_legal_holds')
    op.drop_index(op.f('ix_security_incidents_workspace_id'), table_name='security_incidents')
    op.drop_index('ix_security_incident_state', table_name='security_incidents')
    op.drop_table('security_incidents')
    op.drop_index('ix_security_copyright_sla', table_name='security_copyright_cases')
    op.drop_index(op.f('ix_security_copyright_cases_workspace_id'), table_name='security_copyright_cases')
    op.drop_index(op.f('ix_security_copyright_cases_reported_by'), table_name='security_copyright_cases')
    op.drop_table('security_copyright_cases')
    op.drop_index(op.f('ix_security_compliance_assessments_workspace_id'), table_name='security_compliance_assessments')
    op.drop_index('ix_security_assessment_expiry', table_name='security_compliance_assessments')
    op.drop_table('security_compliance_assessments')
    op.drop_index('ix_operations_component_public', table_name='operations_service_components')
    op.drop_table('operations_service_components')
    op.drop_index('ix_operations_runbook_effective', table_name='operations_runbook_versions')
    op.drop_table('operations_runbook_versions')
    op.drop_index('ix_operations_ga_state', table_name='operations_ga_assessments')
    op.drop_table('operations_ga_assessments')
    op.drop_index('ix_operations_backup_policy_effective', table_name='operations_backup_policy_versions')
    op.drop_table('operations_backup_policy_versions')
    # ### end Alembic commands ###
