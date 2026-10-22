import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Markdown } from './Markdown'

describe('Markdown', () => {
  it('renders bold, code and lists', () => {
    const { container } = render(<Markdown text={'Fee is **INR 0** for `SHP-5001`.\n\n- first\n- second'} />)
    expect(screen.getByText('INR 0').tagName).toBe('STRONG')
    expect(screen.getByText('SHP-5001').tagName).toBe('CODE')
    expect(container.querySelectorAll('li')).toHaveLength(2)
  })

  it('never turns model output into HTML', () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> <script>alert(1)</script>'} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    expect(container.textContent).toContain('<script>alert(1)</script>')
  })
})
