def make_label(team: str, goals: int) -> str:
    label = f"{team}: {goals}"  # ← переменная появляется здесь
    return label.upper()


def main():
    team = "EDM"
    goals = 5
    out = make_label(team, goals)
    print(out)


if __name__ == "__main__":
    main()
