# Web-URL Import Funktionalität

## Übersicht

Das RAG-System verfügt über eine umfassende Web-URL-Import-Funktion, die Inhalte von Webseiten extrahiert und in die Wissensbasis integriert. Die Funktion ist sowohl über die GUI als auch programmatisch verfügbar.

## Funktionsweise

### 1. **GUI-Integration**

#### Zugriff über die Benutzeroberfläche:
- **Button**: "🌐 Web-URLs in RAG importieren" im Setup-Bereich
- **Dialog**: Benutzerfreundlicher URL-Eingabe-Dialog
- **Eingabe**: URLs werden zeilenweise eingegeben (eine URL pro Zeile)
- **Progress**: Fortschrittsanzeige während der Verarbeitung

#### Beispiel GUI-Verwendung:
```
https://wikipedia.org/wiki/Machine_Learning
https://docs.python.org/3/tutorial/
https://example.com/artikel1
```

### 2. **Technische Implementation**

#### Core-Funktion: `upsert_url()`
```python
def upsert_url(self, url: str, *, 
               doc_id: Optional[str] = None,
               metadata: Optional[Dict[str, Any]] = None,
               include_tables: bool = True,
               include_links: bool = False,
               timeout: int = 30) -> bool
```

#### Parameter:
- **url**: Die zu verarbeitende URL (muss mit http:// oder https:// beginnen)
- **doc_id**: Optionale Document-ID (wird automatisch generiert falls leer)
- **metadata**: Zusätzliche Metadaten für das Dokument
- **include_tables**: HTML-Tabellen in Extraktion einbeziehen
- **include_links**: Links in Extraktion einbeziehen  
- **timeout**: HTTP-Timeout in Sekunden (Standard: 30s)

### 3. **Content-Extraktion (Multi-Level-Fallback)**

Das System verwendet ein robustes Multi-Level-Fallback-System:

#### **Level 1: Trafilatura (Primär)**
- **Library**: `trafilatura` - Speziell für Web-Content-Extraktion
- **Features**:
  - Intelligente Hauptcontent-Erkennung
  - Entfernung von Navigation, Werbung, etc.
  - Optionale Tabellen- und Link-Extraktion
  - Formatierung beibehalten
- **Qualität**: Höchste Qualität, beste Bereinigung

#### **Level 2: BeautifulSoup (Fallback)**
- **Library**: `BeautifulSoup4`
- **Features**:
  - HTML-Parsing und Content-Extraktion
  - Entfernung von Script/Style/Navigation-Tags
  - Suche nach main/article/content-Bereichen
  - Whitespace-Bereinigung
- **Verwendung**: Falls trafilatura fehlschlägt

#### **Level 3: Regex-Fallback (Notfall)**
- **Method**: Einfache Regex-basierte HTML-Tag-Entfernung
- **Features**:
  - Entfernung aller HTML-Tags
  - Whitespace-Normalisierung
  - Mindestlängen-Prüfung
- **Verwendung**: Als letzter Ausweg

### 4. **HTTP-Handling**

#### **Robuste Downloads**:
```python
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
response = requests.get(url, timeout=timeout, headers=headers)
```

#### **Features**:
- **User-Agent**: Verhindert Bot-Blocking
- **Timeout**: Konfigurierbare Zeitbegrenzung
- **Status-Prüfung**: HTTP-Fehler werden abgefangen
- **Encoding**: Automatische Encoding-Erkennung

### 5. **Metadaten-Generierung**

Für jede URL werden umfassende Metadaten generiert:

```python
metadata = {
    "content_type": "web",
    "source_url": url,
    "domain": parsed_url.netloc,
    "path": parsed_url.path,
    "extraction_method": "trafilatura|fallback",
    "content_length": len(extracted_text),
    "html_length": len(html_content),
    "extraction_errors": errors,
    "include_tables": include_tables,
    "include_links": include_links
}
```

#### **Metadaten-Felder**:
- **content_type**: Immer "web"
- **source_url**: Original-URL
- **domain**: Domain der Website
- **path**: URL-Pfad
- **extraction_method**: Verwendete Extraktionsmethode
- **content_length**: Länge des extrahierten Texts
- **html_length**: Länge des Original-HTML
- **extraction_errors**: Liste der aufgetretenen Fehler
- **include_tables/links**: Verwendete Optionen

### 6. **Document-ID-Generierung**

```python
# Automatische ID-Generierung aus URL
clean_url = url.replace('://', '_').replace('/', '_').replace('?', '_').replace('#', '_')
doc_id = f"web_{clean_url[:50]}"
```

#### **Features**:
- **Prefix**: "web_" für eindeutige Identifikation
- **URL-Bereinigung**: Ersetzung problematischer Zeichen
- **Längen-Begrenzung**: Maximal 50 Zeichen für DB-Kompatibilität
- **Eindeutigkeit**: Gleiche URLs erhalten gleiche IDs (Deduplication)

### 7. **Integration in RAG-System**

Der extrahierte Content wird über die Standard-`upsert_documents()`-Methode integriert:

```python
docs = [{
    "id": doc_id,
    "text": extracted_text,
    "metadata": metadata
}]
results = self.upsert_documents(docs)
```

#### **Vorteile**:
- **Einheitliche Verarbeitung**: Gleiche Chunking-Strategien wie PDFs
- **Embedding-Generierung**: Automatische Vektor-Erstellung
- **Suchbarkeit**: Sofort über RAG-Suche verfügbar
- **Metadaten-Suche**: Filterung nach Domain, Content-Type, etc.

## Verwendungsbeispiele

### **1. Programmatische Verwendung**

```python
from agent.rag_store import RagStore

store = RagStore()

# Einfacher Import
success = store.upsert_url("https://example.com/artikel")

# Erweiterte Optionen
success = store.upsert_url(
    "https://docs.python.org/3/tutorial/",
    doc_id="python_tutorial",
    metadata={"category": "documentation", "language": "python"},
    include_tables=True,
    include_links=False,
    timeout=60
)
```

### **2. GUI-Verwendung**

1. **Button klicken**: "🌐 Web-URLs in RAG importieren"
2. **URLs eingeben**:
   ```
   https://wikipedia.org/wiki/Artificial_Intelligence
   https://docs.openai.com/api/
   https://python.org/dev/peps/pep-8/
   ```
3. **Importieren**: Fortschritt wird angezeigt
4. **Ergebnis**: Erfolgsmeldung mit Statistiken

### **3. Batch-Import**

```python
urls = [
    "https://docs.python.org/3/tutorial/",
    "https://docs.python.org/3/library/",
    "https://docs.python.org/3/reference/"
]

for url in urls:
    success = store.upsert_url(url)
    print(f"{'✅' if success else '❌'} {url}")
```

## Error-Handling

### **Häufige Fehlerszenarien**:

1. **Ungültige URL**: `url.startswith(('http://', 'https://'))`
2. **Network-Fehler**: Timeout, DNS-Fehler, Connection-Error
3. **HTTP-Fehler**: 404, 403, 500, etc.
4. **Content-Extraktion**: Kein extrahierbarer Content
5. **Encoding-Probleme**: Zeichensatz-Konflikte

### **Robuste Fehlerbehandlung**:

- **Fallback-Kette**: 3-stufiges Fallback-System
- **Error-Logging**: Detaillierte Fehlerprotokollierung
- **Partial-Success**: Einzelne URL-Fehler stoppen nicht den Batch
- **User-Feedback**: Klare Fehlermeldungen in der GUI

## Performance-Optimierung

### **Empfehlungen**:

1. **Batch-Verarbeitung**: Mehrere URLs zusammen verarbeiten
2. **Timeout-Anpassung**: Je nach Website-Geschwindigkeit
3. **Include-Optionen**: Nur benötigte Features aktivieren
4. **Parallel-Processing**: Für große URL-Listen
5. **Caching**: Duplicate-Detection über Document-IDs

### **Typische Performance**:

- **Kleine Artikel**: 1-3 Sekunden
- **Große Seiten**: 5-10 Sekunden  
- **Langsame Sites**: Bis zu Timeout-Limit
- **Batch von 10 URLs**: 1-2 Minuten

## Unterstützte Website-Typen

### **Optimal unterstützt**:
- ✅ **News-Artikel**: BBC, CNN, Zeit.de, etc.
- ✅ **Dokumentation**: docs.python.org, MDN, etc.
- ✅ **Blogs**: Medium, WordPress-Sites
- ✅ **Wikipedia**: Alle Sprachen
- ✅ **Wissenschaftliche Artikel**: ArXiv-abstracts, etc.

### **Teilweise unterstützt**:
- ⚠️ **E-Commerce**: Produktbeschreibungen (viel Rauschen)
- ⚠️ **Social Media**: Twitter/Facebook (Anti-Bot-Maßnahmen)
- ⚠️ **Dynamic Content**: JavaScript-heavy Sites

### **Nicht unterstützt**:
- ❌ **JavaScript-Required**: Single-Page-Apps ohne Server-Rendering
- ❌ **Login-Required**: Passwort-geschützte Inhalte
- ❌ **CAPTCHA-Sites**: Anti-Bot-Systeme
- ❌ **PDF-Links**: Direkte PDF-URLs (nutzen Sie PDF-Import)

## Fazit

Der Web-URL-Import bietet eine robuste, benutzerfreundliche Lösung für die Integration von Web-Content in das RAG-System. Durch das Multi-Level-Fallback-System und die umfassende Metadaten-Generierung wird eine hohe Erfolgsrate und Datenqualität erreicht.
