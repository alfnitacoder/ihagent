import { render, screen } from '@testing-library/react';
import App from './App';

test('renders the task manager', () => {
  render(<App />);
  expect(screen.getByText(/task manager/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/new task/i)).toBeInTheDocument();
});
