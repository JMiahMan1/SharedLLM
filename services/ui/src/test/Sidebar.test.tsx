import { screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import Sidebar from '../components/layout/Sidebar';
import { renderWithProviders } from './render';

describe('Sidebar Component', () => {
  it('renders the brand title', () => {
    renderWithProviders(<Sidebar />);
    
    expect(screen.getByText('Jarvis OS')).toBeInTheDocument();
  });

  it('renders navigation links', () => {
    renderWithProviders(<Sidebar />);
    
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(screen.getByText('Workspaces')).toBeInTheDocument();
    expect(screen.getByText('Media')).toBeInTheDocument();
    expect(screen.getByText('Lab')).toBeInTheDocument();
  });
});
