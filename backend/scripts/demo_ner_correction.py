"""Run the NER correction example with explicit in-memory reference values.

Usage: python -m backend.scripts.demo_ner_correction
"""

if __name__ == "__main__":
    from backend import keyword_extract

    entities = [
        {'text': '컴융부', 'label': 'DEPARTMENT'},
        {'text': '전심과목', 'label': 'CATEGORY'},
    ]
    references = {
        "DEPARTMENT": ["컴퓨터융합학부"],
        "CATEGORY": ["전공(기초)", "전공(핵심)", "전공(심화)"],
    }

    corrected = keyword_extract.correct_ner_entities(entities, references=references)
    for c in corrected:
        print(c)
