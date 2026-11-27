# Pierwsza instalacja Boćka

Potrzebujesz Home Assistant z Assist. Tę wersję sprawdzono na HA Core 2026.11.3.
Głos możesz skonfigurować później. Pierwsze polecenie da się wpisać w Assist.

## Zainstaluj integrację

### Przez Marketplace

1. W Home Assistant otwórz **Ustawienia → Marketplace → Dodaj z linku**.
2. Jeśli HA poprosi o połączenie z GitHub, dokończ logowanie. Dodaj publiczne
   repozytorium [Boćka](https://github.com/gyhdx2nd9x-ai/bociek_HA_integration).
   Jeśli HA pyta o typ, wybierz **Integracja**.
3. Otwórz kartę **Bociek**, wybierz instalację i uruchom ponownie Home Assistant.

Marketplace zastępuje osobny HACS od HA 2026.11.
[Dokumentacja Marketplace](https://www.home-assistant.io/integrations/marketplace/#custom-repositories).

### Ręcznie

1. Pobierz [kod repozytorium](https://github.com/gyhdx2nd9x-ai/bociek_HA_integration)
   lub rozpakuj otrzymaną paczkę.
2. Skopiuj folder `custom_components/polish_context_voice` do katalogu
   `custom_components` obok `configuration.yaml`. W instalacji HA ścieżka
   zwykle będzie wyglądała tak:

   ```text
   /config/custom_components/polish_context_voice/manifest.json
   ```

3. Uruchom ponownie Home Assistant. Integracja zawiera potrzebne pliki
   frontendu i nie wymaga dopisywania niczego do `configuration.yaml`.

## Włącz w Assist

1. Wejdź w **Ustawienia → Urządzenia i usługi → Dodaj integrację** i wybierz
   **HA Polish Context Voice**. Jeśli aktualizujesz Boćka, zachowaj dotychczasowy wpis.
2. W **Ustawienia → Asystenci głosowi** wybierz lub utwórz profil z językiem
   polskim i ustaw **HA Polish Context Voice** jako agenta rozmowy. Jeśli widzisz
   opcję „Preferuj lokalną obsługę poleceń” (*Prefer handling commands locally*),
   wyłącz ją w tym profilu, aby polecenia trafiały do Boćka.
3. W ustawieniach Assist udostępnij wybrane encje, np. jedną lampę. Przypisz
   satelitę do pokoju w Home Assistant. Jeśli pokoje lub urządzenia mają nazwy
   w innym języku, dodaj im polskie aliasy.
4. Wpisz w Assist „Która godzina?”. Następnie spróbuj „Zapal światło w salonie”,
   używając nazwy swojego pokoju. Gdy w pokoju jest kilka lamp, w panelu **Bociek**
   możesz wskazać preferowaną lampę albo zestaw głównych świateł.

Przy aktualizacji ręcznej zachowaj poprzedni folder integracji poza
`custom_components`. Aby się wycofać, przywróć go i uruchom ponownie HA.
W profilu Assist możesz też w każdej chwili wybrać poprzedniego agenta.
