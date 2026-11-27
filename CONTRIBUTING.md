# Współtworzenie Boćka

Do odtworzenia błędu podaj wersję Boćka, HA, oczekiwany wynik i krótkie
polecenie z fikcyjną nazwą pokoju lub urządzenia. Przy zmianie parsera
sprawdź też odmowę, podgląd i zachowanie przy dwóch pasujących celach.

## Zgłoszenie i odtworzenie pomyłki

Po użyciu **Wypróbuj polecenie** wybierz **Opisz pomyłkę na przykładzie**.
Wpisz jedno polecenie o światłach, wybierz fikcyjny pokój satelity i ustal
oczekiwane zachowanie. Przejrzyj JSON i skopiuj go do pliku
`local/zgloszenie.json`. Katalog `local/` utwórz poza śledzonymi plikami
Git. Do pomysłów służy osobna opcja **Zaproponuj funkcję**.

Do problemów z nawiązaniem użyj **Opisz problem w rozmowie**. Formularz
przyjmuje od 2 do 6 wypowiedzi dla jednego fikcyjnego satelity. Przy każdej
można wpisać oczekiwane cele, stan lamp przed wypowiedzią i upływ czasu.
Przy próbie „Zgaś zapalone światła”, „A w salonie?” wskaż źródło w kuchni,
odpowiednio ustaw stan fikcyjnych lamp i opisz wynik każdej wypowiedzi.
Odtworzenie wykonuje podgląd planu. Kolejna wypowiedź dziedziczy stan
fikcyjnych lamp oraz kontekst rozmowy, a zmiany stanu ustawiasz jawnie.

```sh
python -m benchmark.feedback local/zgloszenie.json --output local/odtworzenie.json
```

Polecenie uruchamia próbę na fikcyjnym domu bez HA. Kod 0 oznacza zgodność
wyniku z oczekiwaniem, 1 rozbieżność, a 2 problem z plikiem. Przy rozbieżności
narzędzie wypisuje numery wypowiedzi i pola, które się różnią. Raport zawiera
przypadek i fikcyjny dom. Po sprawdzeniu treści można dodać dom do
`benchmark/corpus/homes/`, a przypadek do tablicy w pliku korpusu regresji.
Raporty z pomysłami są przeznaczone do przeczytania przez człowieka.

## Testy rdzenia

W katalogu projektu:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python -m benchmark.corpus --split regression --strict --output /tmp/bociek-corpus.json
```

Testy nie potrzebują domowego HA. Korpus regresji korzysta z fikcyjnych
pokojów, aliasów i urządzeń. Otwarta ewaluacja jest oddzielna od regresji.

## Natywne laboratorium i instalator próbny

```sh
python3.14 -m venv .ha-venv
.ha-venv/bin/python -m pip install -r lab/requirements.txt
.ha-venv/bin/python -m lab.run --output /tmp/bociek-native.json
.ha-venv/bin/python -m scripts.package_checkpoint /tmp/bociek-native.json
```

Laboratorium startuje na `127.0.0.1:18123` i korzysta z własnego
katalogu pod `.ha-lab/`. Pakowanie wymaga zaliczonego raportu o sumie
zgodnej z drzewem komponentu. ZIP w `dist/` zawiera integrację i instrukcję
prób. Narzędzia programistyczne nie są zależnościami tej integracji w HA.
