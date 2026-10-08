import hashlib
import json


def digest(content):
    fields = {key: content[key] for key in ("age_min", "age_max", "story", "assets", "marketing")}
    fields.update({key: content[key] for key in ("language", "market") if key in content})
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def require_approval(db, content):
    review = db.reviews.find_one(
        {"content_id": content["_id"], "digest": digest(content), "approved": True, "reviewer": "human"}
    )
    if content.get("status") not in ("approved", "published") or not review:
        raise PermissionError("Exact content version requires human age/safety approval")
