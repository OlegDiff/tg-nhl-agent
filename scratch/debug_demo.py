def calc_total(prices: list[int], discount_pct: int) -> int:
    # BUG: тут специально сделана логическая ошибка
    discounted = [p * (100 - discount_pct) / 100 for p in prices]  # должно быть /100
    return sum(discounted)


def main():
    prices = [100, 250, 80]
    discount_pct = 20
    total = calc_total(prices, discount_pct)
    print("total =", total)


if __name__ == "__main__":
    main()
