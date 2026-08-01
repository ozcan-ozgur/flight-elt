# Flight ELT — Veritabani Semasi

OpenSky Network API'den ucak konum verisi cekip Postgres'e yazan, dbt ile
staging/marts katmanlarina donusturen bir ELT pipeline'i. Bu dosya Metabase
uzerinde sorgu yazarken (veya baska bir araca baglam vermek icin) tum
tablo/kolon yapisini tek yerde ozetler.

Katman akisi: `raw_flight_states` (ham, elt.py yazar) + `airlines` seed →
`stg_flight_states` / `stg_airlines` (staging, tipleme+temizlik) →
`dim_airline` / `fct_flight_positions` / `fct_daily_traffic` (marts, BI icin
kullanilacak son tablolar). Materialization: staging = view, marts = table
(bkz. `flight_dbt/dbt_project.yml`).

---

## 1. `raw_flight_states` (ham tablo, Postgres `public` semasi)

`elt.py` tarafindan OpenSky `/api/states/all` cevabindan doldurulur, dbt
disinda yonetilir (`_sources.yml` icinde `source('raw','raw_flight_states')`
olarak referans alinir).

| Kolon | Tip | Aciklama |
|---|---|---|
| id | SERIAL PK | |
| query_time | BIGINT | API cagrisinin epoch zamani (snapshot_at'e donusur) |
| icao24 | TEXT | Ucak transponder adresi |
| callsign | TEXT | Ham cagri isareti (bosluklu/bos gelebilir) |
| origin_country | TEXT | Kayitli ulke |
| time_position | BIGINT | Pozisyon epoch zamani |
| last_contact | BIGINT | Son temas epoch zamani |
| longitude, latitude | DOUBLE PRECISION | |
| baro_altitude, geo_altitude | DOUBLE PRECISION | metre |
| on_ground | BOOLEAN | |
| velocity | DOUBLE PRECISION | m/s |
| true_track | DOUBLE PRECISION | derece |
| vertical_rate | DOUBLE PRECISION | m/s |
| sensors | TEXT | |
| squawk | TEXT | |
| spi | BOOLEAN | |
| position_source | INTEGER | |
| category | INTEGER | |
| ingested_at | TIMESTAMP | DEFAULT now() |

## 2. `airlines` (dbt seed — `flight_dbt/seeds/airlines.csv`)

OpenFlights airline listesi. Kolonlar: `airline_id, name, alias, iata, icao,
callsign, country, active`. `\N` ve `""` degerleri null anlamina gelir
(stg_airlines bunlari temizler).

---

## Staging katmani (`flight_dbt/models/staging/`)

### `stg_flight_states`
Kaynak: `raw_flight_states`. Tipleme + fizik-disi deger temizligi yapar
(~%0.05 satirda gorulen hatali ADS-B okumalari **null'lanir, satir
silinmez**): `velocity > 400 m/s`, `altitude > 16000 m`,
`|vertical_rate| > 100 m/s` esiklerinin uzerindeki degerler null olur.

| Kolon | Kaynak/Not |
|---|---|
| id | not_null, unique |
| icao24 | not_null |
| callsign | trim edilmis, bos ise null |
| origin_country | |
| snapshot_at | `to_timestamp(query_time)`, not_null |
| position_time | `to_timestamp(time_position)` |
| last_contact_time | `to_timestamp(last_contact)` |
| longitude, latitude | -180..180 / -90..90 disinda null |
| baro_altitude, geo_altitude | esik ustu null |
| on_ground | |
| velocity | 0..400 disinda null |
| true_track | 0..360 disinda null |
| vertical_rate | \|x\| ≤ 100 disinda null |
| squawk, spi, position_source, category, ingested_at | oldugu gibi |

### `stg_airlines`
Kaynak: `airlines` seed. `\N`/bos degerleri temizler, `is_active` bool'a
cevirir, `icao_code` null veya `'N/A'` olan satirlari eler.

Kolonlar: `airline_id, airline_name, iata_code, icao_code, country, is_active`

---

## Marts katmani (`flight_dbt/models/marts/`) — Metabase'in gorecegi tablolar

### `dim_airline`
Grain: **1 satir / icao_code**. `stg_airlines`'ta ayni icao_code birden
fazla kayitla geliyorsa (`is_active` oncelikli, sonra `airline_id`) tekiller.

| Kolon | Test |
|---|---|
| icao_code | not_null, unique — join anahtari |
| airline_name | |
| iata_code | |
| country | |
| is_active | |

### `fct_flight_positions`
Grain: **1 satir / ham flight_state gozlemi** (`stg_flight_states` ile
1:1). `stg_flight_states` ile `dim_airline`'i `left(callsign,3) = icao_code`
uzerinden left join eder — bu yuzden `airline_icao` ve `airline_name` cogu
zaman **null** olabilir (callsign yoksa / eslesmiyorsa).

| Kolon | Aciklama |
|---|---|
| flight_state_id | not_null, unique — `stg_flight_states.id` |
| snapshot_at | API cagri zamani |
| icao24 | |
| callsign | |
| callsign_prefix | `left(callsign,3)` — havayolu tahmini |
| airline_icao | FK → `dim_airline.icao_code` (nullable, relationships test var) |
| airline_name | |
| airline_country | dim_airline.country |
| registration_country | stg_flight_states.origin_country |
| latitude, longitude | |
| baro_altitude | |
| velocity | |
| true_track | |
| vertical_rate | |
| on_ground | |
| position_time | |

### `fct_daily_traffic`
Grain: **1 satir / UTC gun**. Pipeline her 30 dakikada bir calisiyor →
gunde 48 snapshot beklenir (`snapshots_per_day = 48`). Index: `snapshot_at`.

Onemli tasarim notu: "gunluk farkli ucak/havayolu/ulke sayisi" kasitli
olarak **yok** — cunku bu metrik snapshot sayisiyla confound olur (cok
ornekleme = yapay yuksek sayi). Bunun yerine snapshot-bazli max/mean
degerler kullaniliyor.

| Kolon | Aciklama |
|---|---|
| traffic_date | UTC gun (PK gibi düşünülebilir) |
| snapshots | O gun kac snapshot alindi |
| coverage_pct | `least(100*snapshots/48, 100)` — gunun ne kadari kapsandi |
| peak_airborne / peak_at | Havada en cok ucak & o anki snapshot zamani |
| trough_airborne / trough_at | En az ucak & zamani |
| mean_airborne | Snapshot basina ortalama havadaki ucak |
| busiest_airline / busiest_airline_aircraft | Gun icinde ortalamada en yogun havayolu |
| median_altitude | Snapshot medyanlarinin ortalamasi |
| median_velocity | Ayni sekilde |
| climb_ratio | `sum(climbing)/sum(descending)` — >1 gokyuzu doluyor, <1 bosaliyor |

---

## Iliskiler / Metabase icin join haritasi

```
dim_airline.icao_code  <──  fct_flight_positions.airline_icao   (left join, nullable)
fct_flight_positions   ──►  fct_daily_traffic  (fct_daily_traffic, fct_flight_positions'tan
                                                  snapshot_at bazinda agregasyonla turetilir,
                                                  dogrudan FK degil)
stg_flight_states.id   ==   fct_flight_positions.flight_state_id
```

- En gunluk/ozet raporlama icin **`fct_daily_traffic`** dogrudan kullanilabilir.
- Ucak/havayolu bazli detay/harita gorunumu icin **`fct_flight_positions`**
  (+ gerekiyorsa `dim_airline` join).
- `airline_icao`/`airline_name` genelde null olabildigi icin Metabase
  filtrelerinde/agregasyonlarinda bunu goz onunde bulundur (ornegin "bilinmeyen
  havayolu" kategorisi eklemek gerekebilir).
