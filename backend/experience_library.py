"""Local, reviewed engineering lessons; immutable sources and explicit decision history."""

from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime


class ExperienceProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)
    lesson: str = Field(min_length=10, max_length=1600)
    route_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExperienceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1)
    decision: Literal["approved", "rejected", "retired"]
    reviewer: str = Field(min_length=1, max_length=100)
    source_reference: str = Field(min_length=5, max_length=300)
    rationale: str = Field(min_length=5, max_length=1200)
    valid_until: AwareDatetime | None = None


def applicability(request):
    return {
        "material": request["material"],
        "blank_type": request.get("blank_type", "solid"),
        "heat_treatment": request.get("global_requirements", {}).get("heat_treatment", "none"),
        "feature_types": sorted({f["feature_type"] for f in request.get("features", [])}),
    }


def now():
    return datetime.now(timezone.utc)


class ExperienceLibrary:
    def __init__(self, store):
        self.store = store
        with store.lock:
            store.connection.execute(
                "CREATE TABLE IF NOT EXISTS engineering_experiences "
                "(experience_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            store.connection.commit()

    def _write(self, card):
        self.store.connection.execute(
            "INSERT INTO engineering_experiences VALUES (?, ?) "
            "ON CONFLICT(experience_id) DO UPDATE SET payload=excluded.payload",
            (card["experience_id"], json.dumps(card, ensure_ascii=False)),
        )
        self.store.connection.commit()
        self.store._secure_files()

    def list(self, source_job_id=None):
        with self.store.lock:
            rows = self.store.connection.execute(
                "SELECT payload FROM engineering_experiences ORDER BY rowid DESC"
            ).fetchall()
            cards = [json.loads(row[0]) for row in rows]
            return [
                card
                for card in cards
                if source_job_id is None or card["source"]["job_id"] == source_job_id
            ]

    def get(self, experience_id):
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT payload FROM engineering_experiences WHERE experience_id=?",
                (experience_id,),
            ).fetchone()
            if row is None:
                raise KeyError(experience_id)
            return json.loads(row[0])

    def propose(self, job_id, proposal: ExperienceProposal):
        from agents.specialists import route_fingerprint

        with self.store.lock:
            job = self.store.get(job_id)
            result = job.get("custom_result") or job.get("result") or {}
            route = result.get("process_route", [])
            if job["status"] not in {"completed", "failed"} or not route:
                raise ValueError(
                    "A completed or failed route is required for an experience proposal"
                )
            if job.get("harness", {}).get("active"):
                raise ValueError("Wait until route execution finishes before proposing experience")
            fingerprint = route_fingerprint(route)
            if proposal.route_fingerprint != fingerprint:
                raise ValueError("Route changed; reload before proposing experience")
            if len(self.list()) >= 2000:
                raise ValueError("Experience library capacity reached")
            source = {
                "job_id": job_id,
                "route_revision": job.get("route_revision", 0),
                "route_fingerprint": fingerprint,
                "request_digest": hashlib.sha256(
                    json.dumps(job["request"], sort_keys=True).encode()
                ).hexdigest(),
                "verification_conclusion": result.get("verification", {}).get("conclusion"),
                "constraint_counterexamples": result.get("verification", {})
                .get("process_state", {})
                .get("counterexamples", []),
                "findings": result.get("agent_collaboration", {}).get("findings", []),
                "engineering_skills_digest": job.get("skill_snapshot", {}).get("digest"),
            }
            card = {
                "experience_id": uuid4().hex,
                "version": 1,
                "status": "proposed",
                "title": proposal.title,
                "lesson": proposal.lesson,
                "applicability": applicability(job["request"]),
                "source": source,
                "created_at": now().isoformat(),
                "decisions": [],
                "authority": "reference_advice",
                "production_release": False,
            }
            self._write(card)
            return card

    def review(self, experience_id, decision: ExperienceDecision):
        from agents.specialists import route_fingerprint

        with self.store.lock:
            card = self.get(experience_id)
            if card["version"] != decision.expected_version:
                raise ValueError("Experience version changed; reload before reviewing")
            allowed = {"proposed": {"approved", "rejected"}, "approved": {"retired"}}
            if decision.decision not in allowed.get(card["status"], set()):
                raise ValueError("Invalid experience review transition")
            if decision.decision == "approved":
                if decision.valid_until is None or decision.valid_until <= now():
                    raise ValueError("Approval requires a future validity deadline")
                # Approval is bound to the source route revision visible to this reviewer.
                job = self.store.get(card["source"]["job_id"])
                result = job.get("custom_result") or job.get("result") or {}
                if (
                    job.get("route_revision", 0) != card["source"]["route_revision"]
                    or route_fingerprint(result.get("process_route", []))
                    != card["source"]["route_fingerprint"]
                    or job.get("harness", {}).get("active")
                ):
                    raise ValueError(
                        "Source route changed; create a new proposal for the current revision"
                    )
            card["status"] = decision.decision
            card["version"] += 1
            card["decisions"].append(
                {
                    **decision.model_dump(mode="json"),
                    "at": now().isoformat(),
                    "reviewer_identity": "local_operator_supplied",
                }
            )
            self._write(card)
            return card

    def active(self):
        timestamp = now()
        return [
            card
            for card in self.list()
            if card["status"] == "approved"
            and datetime.fromisoformat(card["decisions"][-1]["valid_until"].replace("Z", "+00:00"))
            > timestamp
        ]

    def snapshot(self, request, remote):
        """Pin applicable reviewed lessons together with the initial Tencent snapshot."""
        scope = applicability(request)
        items = list(remote.get("items", []))
        local_items = []
        for card in self.active():
            if card["applicability"] != scope:
                continue
            decision = card["decisions"][-1]
            item = {
                "id": card["experience_id"],
                "version": card["version"],
                "evidence_id": f"experience:{card['experience_id']}@v{card['version']}",
                "content": card["lesson"],
                "source": decision["source_reference"],
                "reviewer": decision["reviewer"],
                "valid_until": decision["valid_until"],
                "applicability": card["applicability"],
                "route_fingerprint": card["source"]["route_fingerprint"],
                "authority": "reviewed_reference_advice",
            }
            if len(json.dumps(items + local_items + [item], ensure_ascii=False)) > 6000:
                continue
            local_items.append(item)
            if len(local_items) == 3:
                break
        result = {
            **remote,
            "items": items + local_items,
            "local_experience_count": len(local_items),
        }
        if local_items:
            result.update(
                provider="local-reviewed-experience+tencentdb-agent-memory-v3",
                status="retrieved",
                authority="reference_advice",
            )
        result.pop("digest", None)
        result["digest"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        return result
