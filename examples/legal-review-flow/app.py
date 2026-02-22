"""
Legal Review Flow - SWP example (Python).
States: INITIAL -> UPLOAD -> ANALYZING -> REVIEW -> COMPLETED | FAILED
"""
import sys
from pathlib import Path

# Add SDK to path when run from examples
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sdks" / "python"))

from swp import (
    SWPWorkflow,
    TransitionDef,
    create_app,
)
from fastapi.responses import FileResponse
from fastapi.exceptions import HTTPException
import uvicorn

# FSM: initial -> upload (submit_doc) -> analyzing (auto) -> review (approve/reject) -> completed/failed
transitions = [
    TransitionDef(from_state="INITIAL", action="start", to_state="UPLOAD"),
    TransitionDef(from_state="UPLOAD", action="submit_doc", to_state="ANALYZING", expects={"file_url": "string"}),
    TransitionDef(from_state="ANALYZING", action="complete_analysis", to_state="REVIEW"),
    TransitionDef(from_state="REVIEW", action="approve", to_state="COMPLETED"),
    TransitionDef(from_state="REVIEW", action="reject", to_state="FAILED", expects={"reason": "string"}),
]

workflow = (
    SWPWorkflow("legal-review-v1", "INITIAL", transitions, base_url="http://localhost:8000")
    .hint("INITIAL", "Start the legal review workflow. Use the 'start' action.")
    .hint("UPLOAD", "Upload a document. Use the 'submit_doc' action with file_url (string).")
    .hint("ANALYZING", "Analysis in progress. Wait for the stream or poll GET /runs/{run_id}, then call complete_analysis.")
    .hint("REVIEW", "Review the audit results. Use 'approve' to complete or 'reject' with a reason.")
    .hint("COMPLETED", "Workflow completed successfully.")
    .hint("FAILED", "Workflow ended in failure.")
    .skill("UPLOAD", "document-upload-skill", "document-upload-skill/SKILL.md")
    .skill("REVIEW", "approval-skill", "approval-skill/SKILL.md")
    .status_default("ANALYZING", "processing")
)

# Serve skills from repo root so active_skill.url can be fetched
app = create_app(workflow)
SKILLS_DIR = ROOT / "skills"

@app.get("/skills/{path:path}")
def serve_skill(path: str):
    full = SKILLS_DIR / path
    if not full.is_file() or SKILLS_DIR not in full.resolve().parents:
        raise HTTPException(status_code=404)
    return FileResponse(full)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
