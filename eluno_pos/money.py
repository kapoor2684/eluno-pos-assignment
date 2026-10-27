"""Money is integer paise everywhere inside the core. Strings only at the adapter boundary."""
from decimal import Decimal

def to_paise(s) -> int:
    return int((Decimal(str(s)) * 100).to_integral_value())

def to_str(p: int) -> str:
    return f"{'-' if p < 0 else ''}{abs(p) // 100}.{abs(p) % 100:02d}"

def inr(p: int) -> str:
    s = to_str(abs(p)); whole, frac = s.split(".")
    return f"{'-' if p < 0 else ''}₹{int(whole):,}.{frac}"

def div_half_up(num: int, den: int) -> int:
    """round-half-up of num/den for num>=0, den>0, in pure integers."""
    return (2 * num + den) // (2 * den)

def allocate(total: int, weights):
    """Split `total` across weights proportionally (largest remainder) so shares sum exactly to total."""
    W = sum(weights)
    if total == 0 or W == 0: return [0] * len(weights)
    base = [total * w // W for w in weights]
    rem = sorted(range(len(weights)), key=lambda i: (-(total * weights[i] % W), i))
    for i in rem[: total - sum(base)]: base[i] += 1
    return base
