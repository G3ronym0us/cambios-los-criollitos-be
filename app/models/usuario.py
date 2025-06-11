from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .changes import Changes

class Usuario:
    def __init__(self, name: str, phone: str):
        self.name = name
        self.phone = phone
        self.changes: List['Changes'] = []

    def add_change(self, change: 'Changes'):
        self.changes.append(change)