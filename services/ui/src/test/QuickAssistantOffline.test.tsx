import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import QuickAssistantWidget from '../components/widgets/QuickAssistantWidget';
import { api } from '../services/api';

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'alex' } }),
  __esModule: true,
}));

const OFFLINE = "Jarvis's thinking brain (the local AI) is offline right now, so I can still control the house.";

describe('Quick Assistant while the local AI is offline', () => {
  it('shows the plain offline sentence as the reply and keeps the question', async () => {
    vi.spyOn(api, 'chat').mockRejectedValueOnce(Object.assign(new Error('Request failed with status code 503'), {
      response: { status: 503, data: {
        status: 'ERROR', message: { role: 'assistant', content: OFFLINE }, error: OFFLINE,
        error_code: 'local_ai_offline', error_id: 'abcd1234', offline: 'local_ai',
      } },
    }));
    render(<QuickAssistantWidget settingsButton={null} userSettings={{ config: {} } as never} onTogglePin={() => {}} />);
    fireEvent.change(screen.getByLabelText('Message the assistant'), { target: { value: 'What is a parable?' } });
    fireEvent.submit(screen.getByLabelText('Message the assistant').closest('form')!);
    const notice = await screen.findByTestId('assistant-notice');
    expect(notice).toHaveTextContent(OFFLINE);
    expect(screen.getByText('What is a parable?')).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Failed to send|11434|Cannot connect/);
  });
});
