import { describe, expect, it, beforeEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { readSendAsPref, useConversationSendAs, useSendAsPref } from './sendAsPref';

describe('send-as preferences', () => {
  beforeEach(() => localStorage.clear());

  it('notes do not inherit the chat choice', () => {
    // The bug: Admin picked in chat made Notes open the Admin account's notes.
    localStorage.setItem('jarvis-talk-send-as', 'admin');
    expect(readSendAsPref('notes')).toBe('me');
    const { result } = renderHook(() => useSendAsPref(true, 'notes'));
    expect(result.current[0]).toBe('me');
  });

  it('a notes choice is remembered for notes only', () => {
    const { result } = renderHook(() => useSendAsPref(true, 'notes'));
    act(() => result.current[1]('admin'));
    expect(readSendAsPref('notes')).toBe('admin');
    expect(readSendAsPref('talk')).toBe('me');
  });

  it('a chat choice applies to that conversation only', () => {
    const family = renderHook(() => useConversationSendAs(true, 'room-family'));
    act(() => family.result.current[1]('admin'));
    expect(family.result.current[0]).toBe('admin');

    const ops = renderHook(() => useConversationSendAs(true, 'room-ops'));
    expect(ops.result.current[0]).toBe('me');
  });

  it('a non-admin is always themselves', () => {
    localStorage.setItem('jarvis-talk-send-as-by-room', JSON.stringify({ 'room-family': 'admin' }));
    const { result } = renderHook(() => useConversationSendAs(false, 'room-family'));
    expect(result.current[0]).toBe('me');
  });
});
