import { render } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { describe, it, expect } from 'vitest';
import Sidebar from '../components/layout/Sidebar';

describe('Sidebar Component', () => {
  it('renders the brand title', () => {
    const { getByText } = render(
      <BrowserRouter>
        <Sidebar />
      </BrowserRouter>
    );
    
    expect(getByText('Nexus OS')).toBeInTheDocument();
  });

  it('renders navigation links', () => {
    const { getByText } = render(
      <BrowserRouter>
        <Sidebar />
      </BrowserRouter>
    );
    
    expect(getByText('Dashboard')).toBeInTheDocument();
    expect(getByText('Admin')).toBeInTheDocument();
    expect(getByText('Nexus Lab')).toBeInTheDocument();
  });
});
