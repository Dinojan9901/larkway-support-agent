import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { vi } from 'vitest'
import type { ApiClient } from '../api/client'
import { ApiContext } from '../api/context'
import { AuthContext, type AuthState } from '../auth/context'
import { customer, fakeApi } from './fakeApi'

export function renderWith(ui: ReactElement, { api = fakeApi(), auth = {} }: { api?: ApiClient; auth?: Partial<AuthState> } = {}) {
  const authState: AuthState = {
    user: customer,
    signIn: vi.fn<AuthState['signIn']>(async () => {}),
    signOut: vi.fn<AuthState['signOut']>(),
    signOutReason: null,
    ...auth,
  }
  return {
    api,
    auth: authState,
    ...render(
      <ApiContext.Provider value={api}>
        <AuthContext.Provider value={authState}>{ui}</AuthContext.Provider>
      </ApiContext.Provider>,
    ),
  }
}
