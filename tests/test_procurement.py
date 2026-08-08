from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app


def test_full_procurement_flow():
    with TestClient(app) as client:
        seed = client.post("/api/demo/seed")
        assert seed.status_code == 200
        bootstrap = client.get("/api/bootstrap").json()
        payload = {
            "companyId": bootstrap["companies"][0]["id"],
            "constructionSiteId": bootstrap["sites"][0]["id"],
            "requestedBy": bootstrap["users"][0]["id"],
            "requestTitle": "Materiais do bloco A",
            "requiredAt": (date.today() + timedelta(days=5)).isoformat(),
            "priority": "HIGH",
            "commercialConstraints": {"paymentTerms": "NET_28"},
            "items": [
                {
                    "clientItemId": "item-1",
                    "rawDescription": "Cimento CP-II de 50 kg",
                    "quantity": 100,
                    "unit": "BAG",
                },
                {
                    "clientItemId": "item-2",
                    "rawDescription": "Argamassa AC-II de 20 kg",
                    "quantity": 30,
                    "unit": "BAG",
                },
            ],
        }
        created = client.post("/api/purchase-requests", json=payload)
        assert created.status_code == 201
        request_id = created.json()["id"]

        processed = client.post(f"/api/purchase-requests/{request_id}/process")
        assert processed.status_code == 200
        body = processed.json()
        if body["status"] == "CLARIFYING":
            answers = [
                {"clarificationId": question["id"], "value": question["options"][0] if question["options"] else "Conforme projeto"}
                for question in body["clarifications"]
                if question["status"] == "WAITING_USER"
            ]
            resumed = client.post(
                f"/api/purchase-requests/{request_id}/clarifications",
                json={"answers": answers},
            )
            assert resumed.status_code == 200
            body = resumed.json()

        assert body["status"] == "SUPPLIERS_SELECTED"
        assert len([supplier for supplier in body["supplierSelections"] if supplier["selected"]]) >= 2
