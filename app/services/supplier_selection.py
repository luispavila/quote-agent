from dataclasses import dataclass

from app.models import Supplier


@dataclass
class CandidateResult:
    supplier: Supplier
    eligible: bool
    selected: bool
    score: float | None
    risk_level: str
    factors: dict
    reasons: list[str]
    exclusion_reason: str | None = None


def _city_supported(supplier: Supplier, city: str) -> bool:
    normalized = city.casefold().strip()
    return any(str(value).casefold().strip() == normalized for value in supplier.service_cities)


def _coverage(supplier: Supplier, categories: set[str]) -> float:
    supported = {str(value).upper() for value in supplier.categories}
    return len(categories & supported) / len(categories) if categories else 0


def evaluate_supplier(supplier: Supplier, categories: set[str], city: str) -> CandidateResult:
    if supplier.status != "ACTIVE":
        return CandidateResult(supplier, False, False, None, "HIGH", {}, [], "SUPPLIER_NOT_ACTIVE")
    if supplier.blocked:
        return CandidateResult(supplier, False, False, None, "HIGH", {}, [], "SUPPLIER_BLOCKED")
    if not supplier.phone:
        return CandidateResult(supplier, False, False, None, "HIGH", {}, [], "NO_USABLE_CONTACT")
    if not _city_supported(supplier, city):
        return CandidateResult(supplier, False, False, None, "HIGH", {}, [], "REGION_NOT_SUPPORTED")
    coverage = _coverage(supplier, categories)
    if coverage == 0:
        return CandidateResult(supplier, False, False, None, "HIGH", {}, [], "CATEGORY_NOT_SUPPORTED")

    performance = supplier.performance or {}
    orders = int(performance.get("completedOrders", 0))
    factors = {
        "coverage": coverage,
        "onTimeDelivery": float(performance.get("onTimeDeliveryRate", .5)),
        "quoteResponseRate": float(performance.get("quoteResponseRate", .5)),
        "orderAccuracy": float(performance.get("orderAccuracyRate", .5)),
        "responseSpeed": float(performance.get("responseSpeedScore", .5)),
        "priceCompetitiveness": float(performance.get("priceCompetitiveness", .5)),
        "logistics": float(performance.get("logisticsScore", .75)),
    }
    score = (
        factors["coverage"] * .30 + factors["onTimeDelivery"] * .20 +
        factors["quoteResponseRate"] * .15 + factors["orderAccuracy"] * .10 +
        factors["responseSpeed"] * .10 + factors["priceCompetitiveness"] * .10 +
        factors["logistics"] * .05
    )
    risk = "MEDIUM" if orders < 3 else "LOW"
    reasons = [f"Atende {round(coverage * 100)}% das categorias do pedido.", "Entrega na região da obra."]
    if orders < 3:
        reasons.append("Fornecedor novo, incluído como exploração controlada.")
    else:
        reasons.append(f"{round(factors['onTimeDelivery'] * 100)}% de pontualidade histórica.")
    return CandidateResult(supplier, True, False, round(score, 4), risk, factors, reasons)


def select_suppliers(suppliers: list[Supplier], categories: set[str], city: str, limit: int = 3) -> list[CandidateResult]:
    results = [evaluate_supplier(supplier, categories, city) for supplier in suppliers]
    eligible = sorted((result for result in results if result.eligible), key=lambda result: result.score or 0, reverse=True)
    selected: list[CandidateResult] = []
    new_supplier_used = False
    for result in eligible:
        if len(selected) >= limit:
            break
        if result.risk_level == "MEDIUM" and new_supplier_used:
            continue
        result.selected = True
        selected.append(result)
        new_supplier_used = new_supplier_used or result.risk_level == "MEDIUM"
    return results
