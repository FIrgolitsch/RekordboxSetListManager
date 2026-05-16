# Transition Notes

A **transition note** is freeform text attached to the gap between two adjacent tracks.  Use it to write anything that helps you navigate the mix live:

- Key change notes ("Modulate from Am to F#m")
- EQ tips ("low-cut the incoming track until bar 8")
- Cue point reminders
- Energy arc notes

---

## Writing a note

1. Click a track in the section view to select it.
2. The **Transition Note** tab in the bottom panel becomes active.
3. Type your note.  It is saved automatically when you click away or move to another track.

The note describes the transition *from* the selected track *to* the next track in the section.  The last track in a section can still have a note (e.g. for the transition into the next section).

---

## Viewing notes

Notes are visible only in the Rekordbox Set List Manager UI.  They are **not** exported to Rekordbox XML.  If you want to carry notes into Rekordbox, consider writing abbreviated versions in the track's **Comments** field via the Fix Match dialog.

---

## Clearing a note

Select the track and delete all text in the Transition Note panel.  The empty note is saved with the project.

---

## Notes in the project file

Transition notes are stored as plain text in the `.setmgr` project file alongside the track that precedes the transition.  They survive save/load, undo/redo, and autosave cycles.
