# Korpus polskich poleceń

Korpus używa trzech fikcyjnych domów z różnymi nazwami obszarów i aliasami.
Scenariusz opisuje żądanie, oczekiwane cele, planowane usługi i odpowiedź.
Scenariusze rozmowy obejmują kolejne tury, doprecyzowania i zmiany metadanych.

- `engine.json` i `dialogue.json` opisują polecenia i kontekst rozmowy.
- `parsers.json` i `capabilities.json` sprawdzają odczyt intencji.
- `d01.json` i `d02.json` obejmują zaimki, powtórzenia i mowę w tle.
- `context_choices.json` sprawdza, kiedy odpowiedź „ta druga” wskazuje
  propozycję z bieżącej rozmowy.
- `evaluation.json` to zamrożone przypadki ewaluacyjne. Poprawki gramatyki
  powinny poprawiać ich wynik przy niezmienionych oczekiwaniach.

Bramka CI korzysta ze zbioru `regression`. `evaluation` służy do badania
luk. Oba zbiory uruchamiają się offline, a planowane działania zapisuje
lokalny rejestrator. Konkretne przypadki można odtworzyć, podając ich
identyfikatory w `--case`.

```sh
python -m benchmark.corpus --split regression --case CTX-001 --case CTX-002 --strict --output /tmp/bociek-dialogue.json
```

Więcej w [instrukcji współtworzenia](../../CONTRIBUTING.md).
