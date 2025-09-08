from fastapi import FastAPI
from pydantic import BaseModel
from typing import List

app = FastAPI()

class Note(BaseModel):
    id: str
    title: str
    content: str
    createdAt: str
    updatedAt: str

class NoteInput(BaseModel):
    title: str
    content: str

_NOTES: List[Note] = []

@app.get('/health')
def health():
    return { 'ok': True }

@app.get('/notes', response_model=List[Note])
def list_notes():
    return _NOTES

@app.post('/notes', response_model=Note, status_code=201)
def create_note(inp: NoteInput):
    from datetime import datetime
    now = datetime.utcnow().isoformat()+'Z'
    n = Note(id=str(len(_NOTES)+1), title=inp.title, content=inp.content, createdAt=now, updatedAt=now)
    _NOTES.append(n)
    return n

@app.get('/notes/{id}', response_model=Note)
def get_note(id: str):
    for n in _NOTES:
        if n.id == id:
            return n
    return Note(id='0', title='not found', content='', createdAt='', updatedAt='')

@app.put('/notes/{id}', response_model=Note)
def update_note(id: str, inp: NoteInput):
    from datetime import datetime
    for i, n in enumerate(_NOTES):
        if n.id == id:
            upd = n.copy(update={'title': inp.title, 'content': inp.content, 'updatedAt': datetime.utcnow().isoformat()+'Z'})
            _NOTES[i] = upd
            return upd
    return get_note(id)

@app.delete('/notes/{id}', status_code=204)
def delete_note(id: str):
    global _NOTES
    _NOTES = [n for n in _NOTES if n.id != id]
    return None
