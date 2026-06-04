/* eslint-disable @typescript-eslint/no-explicit-any */
declare module 'react-syntax-highlighter' {
  import * as React from 'react';
  export class Prism extends React.Component<any, any> {}
  export class Light extends React.Component<any, any> {}
}

declare module 'react-syntax-highlighter/dist/esm/styles/prism' {
  export const atomDark: any;
}
