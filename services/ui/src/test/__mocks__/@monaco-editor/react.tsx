/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable @typescript-eslint/no-unused-vars */
/* eslint-disable react-refresh/only-export-components */
import type { ComponentType } from 'react';

const Editor: ComponentType<any> = (props: Record<string, any>) => (
  <div data-testid="monaco-editor-mock" {...props}>
    <div className="flex items-center justify-center h-full text-slate-500 text-sm">
      Monaco Editor (mocked)
    </div>
  </div>
);

const DiffEditor: ComponentType<any> = (_props: Record<string, any>) => (
  <div data-testid="monaco-diff-editor-mock">
    Monaco Diff Editor (mocked)
  </div>
);

const useMonaco = () => null;

const loader = {
  default: {
    init: () => Promise.resolve({} as any),
    config: () => {},
  },
};

export { Editor, DiffEditor, useMonaco, loader };
export default Editor;
