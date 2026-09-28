import React, { useEffect, useState } from 'react';

const STORAGE_KEY = 'hfagent-tasks';

function loadTasks() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function TaskManager() {
  const [tasks, setTasks] = useState(loadTasks);
  const [newTask, setNewTask] = useState('');
  const [editingId, setEditingId] = useState(null);
  const [draft, setDraft] = useState('');

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
  }, [tasks]);

  const addTask = () => {
    const text = newTask.trim();
    if (!text) {
      return;
    }
    setTasks([...tasks, { id: Date.now(), text, completed: false }]);
    setNewTask('');
  };

  const toggleComplete = (id) => {
    setTasks(
      tasks.map((task) =>
        task.id === id ? { ...task, completed: !task.completed } : task
      )
    );
  };

  const deleteTask = (id) => {
    setTasks(tasks.filter((task) => task.id !== id));
    if (editingId === id) {
      setEditingId(null);
      setDraft('');
    }
  };

  const startEdit = (task) => {
    setEditingId(task.id);
    setDraft(task.text);
  };

  const saveEdit = () => {
    const text = draft.trim();
    if (!text || editingId == null) {
      setEditingId(null);
      return;
    }
    setTasks(
      tasks.map((task) => (task.id === editingId ? { ...task, text } : task))
    );
    setEditingId(null);
    setDraft('');
  };

  const cancelEdit = () => {
    setEditingId(null);
    setDraft('');
  };

  return (
    <div className="task-manager">
      <form
        className="task-form"
        onSubmit={(event) => {
          event.preventDefault();
          addTask();
        }}
      >
        <input
          type="text"
          value={newTask}
          onChange={(event) => setNewTask(event.target.value)}
          placeholder="Add a new task"
          aria-label="New task"
        />
        <button type="submit">Add</button>
      </form>
      {tasks.length === 0 ? (
        <p className="empty">No tasks yet. Add one above.</p>
      ) : (
        <ul className="task-list">
          {tasks.map((task) => (
            <li key={task.id} className={task.completed ? 'done' : ''}>
              <input
                type="checkbox"
                checked={task.completed}
                onChange={() => toggleComplete(task.id)}
                aria-label={`Complete ${task.text}`}
              />
              {editingId === task.id ? (
                <input
                  className="edit-input"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onBlur={saveEdit}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault();
                      saveEdit();
                    }
                    if (event.key === 'Escape') {
                      cancelEdit();
                    }
                  }}
                  autoFocus
                  aria-label="Edit task"
                />
              ) : (
                <span
                  className="task-text"
                  onDoubleClick={() => startEdit(task)}
                >
                  {task.text}
                </span>
              )}
              <button type="button" onClick={() => startEdit(task)}>
                Edit
              </button>
              <button
                type="button"
                className="danger"
                onClick={() => deleteTask(task.id)}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default TaskManager;
