# Próby Boćka 0.10.0b30

Skopiuj folder `custom_components/polish_context_voice` do konfiguracji
testowego HA i uruchom go ponownie. Przy aktualizacji zachowaj kopię starego
folderu poza `custom_components`, aby móc go przywrócić. W polskim profilu
Assist wybierz agenta **HA Polish Context Voice**.

| Próba | Oczekiwany wynik |
| --- | --- |
| W Assist wpisz „Płyną po niebie kolorowe obłoki”. | „Nie rozumiem.” |
| Wskaż z nazwy udostępnioną lampę, której encja ma w testowym HA stan `unavailable`. | „Nie mogę ci z tym pomóc.” Bociek nie wywołuje akcji na lampie. |
| Poproś o zwykłe włączenie sprawnej lampy. | Lampa się włącza i słychać krótkie potwierdzenie, zgodnie z ustawieniami ciszy. |
| Z satelity poproś o światło w bieżącym pokoju, a potem jawnie wskaż inny pokój. | Pierwsza komenda korzysta z pokoju satelity, druga z nazwy wypowiedzianej w poleceniu. |
