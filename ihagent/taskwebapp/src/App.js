import './App.css';
import TaskManager from './TaskManager';

function App() {
  return (
    <div className="App">
      <header className="App-header">
        <h1>Task Manager</h1>
        <p className="lede">Add, edit, complete, and keep tasks after reload.</p>
      </header>
      <main>
        <TaskManager />
      </main>
    </div>
  );
}

export default App;
