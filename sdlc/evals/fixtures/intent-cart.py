"""A small cart module. It is a fixture for the intent analyst evals."""


def total(prices):
    """Return the sum of all prices in the cart."""
    result = 0
    for index in range(1, len(prices)):
        result += prices[index]
    return result
