//! Optional, authority-free Wilder recurrence; Python defines the arithmetic order.

use core::slice;

struct AdxInputs<'a> {
    ranges: &'a [f64],
    plus: &'a [f64],
    minus: &'a [f64],
    period: usize,
    seeds: [f64; 3],
}

struct AdxOutputs<'a> {
    adx: &'a mut [f64],
    plus: &'a mut [f64],
    minus: &'a mut [f64],
    dx: &'a mut [f64],
}

fn wilder_adx(inputs: AdxInputs<'_>, outputs: AdxOutputs<'_>) -> Result<(), i64> {
    let n = inputs.ranges.len() + 1;
    if inputs.plus.len() + 1 != n
        || inputs.minus.len() + 1 != n
        || outputs.adx.len() != n
        || outputs.plus.len() != n
        || outputs.minus.len() != n
        || outputs.dx.len() != n
    {
        return Err(-1);
    }
    if !(2..=10_000).contains(&inputs.period) || inputs.period >= n {
        return Err(-2);
    }
    if inputs.seeds.iter().any(|x| !x.is_finite() || *x < 0.0) {
        return Err(-3);
    }
    if inputs
        .ranges
        .iter()
        .chain(inputs.plus)
        .chain(inputs.minus)
        .any(|x| !x.is_finite() || *x < 0.0)
    {
        return Err(-4);
    }
    outputs.adx.fill(f64::NAN);
    outputs.plus.fill(f64::NAN);
    outputs.minus.fill(f64::NAN);
    outputs.dx.fill(f64::NAN);
    let [mut smoothed_tr, mut smoothed_plus, mut smoothed_minus] = inputs.seeds;
    let period = inputs.period;
    let divisor = period as f64;
    let mut dx_seed = 0.0;
    let mut last_adx = 0.0;
    for i in period..n {
        if i > period {
            // Keep Python's association order; do not use mul_add or reassociate.
            smoothed_tr += inputs.ranges[i - 1] - smoothed_tr / divisor;
            smoothed_plus += inputs.plus[i - 1] - smoothed_plus / divisor;
            smoothed_minus += inputs.minus[i - 1] - smoothed_minus / divisor;
        }
        if !smoothed_tr.is_finite() || !smoothed_plus.is_finite() || !smoothed_minus.is_finite() {
            return Err(-5);
        }
        if smoothed_tr > 0.0 {
            outputs.plus[i] = 100.0 * smoothed_plus / smoothed_tr;
            outputs.minus[i] = 100.0 * smoothed_minus / smoothed_tr;
        } else {
            outputs.plus[i] = 0.0;
            outputs.minus[i] = 0.0;
        }
        let total = outputs.plus[i] + outputs.minus[i];
        outputs.dx[i] = if total > 0.0 {
            100.0 * (outputs.plus[i] - outputs.minus[i]).abs() / total
        } else {
            0.0
        };
        if i < 2 * period - 1 {
            dx_seed += outputs.dx[i];
        } else if i == 2 * period - 1 {
            last_adx = (dx_seed + outputs.dx[i]) / divisor;
            outputs.adx[i] = last_adx;
        } else {
            last_adx = (last_adx * (period - 1) as f64 + outputs.dx[i]) / divisor;
            outputs.adx[i] = last_adx;
        }
    }
    Ok(())
}

/// Additive ABI-1 entry point for ADX/DMI; the original simulation ABI is unchanged.
/// Seeds are computed by Python's math.fsum, not an approximate native sum.
/// A negative status invalidates every output, including any partially computed data.
///
/// # Safety
/// Inputs must be aligned, initialized readable arrays of n-1 f64 elements.
/// Outputs must be aligned writable arrays of n f64 elements. Every output
/// must be disjoint from every other array, including the inputs. All buffers
/// must remain valid for the entire call; null and impossible lengths are rejected.
#[no_mangle]
pub unsafe extern "C" fn axiom_wilder_adx(
    ranges: *const f64,
    plus: *const f64,
    minus: *const f64,
    n: u64,
    period: u64,
    seed_tr: f64,
    seed_plus: f64,
    seed_minus: f64,
    out_adx: *mut f64,
    out_plus: *mut f64,
    out_minus: *mut f64,
    out_dx: *mut f64,
) -> i64 {
    if ranges.is_null()
        || plus.is_null()
        || minus.is_null()
        || out_adx.is_null()
        || out_plus.is_null()
        || out_minus.is_null()
        || out_dx.is_null()
    {
        return -8;
    }
    let Ok(n) = usize::try_from(n) else {
        return -1;
    };
    if n < 2 || n > isize::MAX as usize / core::mem::size_of::<f64>() {
        return -1;
    }
    if !(2..=10_000).contains(&period) || period >= n as u64 {
        return -2;
    }
    // SAFETY: the caller guarantees aligned, non-overlapping buffers with these
    // lengths. Null pointers, integer conversion and slice byte bounds were
    // checked above; no slices outlive this FFI call.
    let (ranges, plus, minus, adx, out_plus, out_minus, dx) = unsafe {
        (
            slice::from_raw_parts(ranges, n - 1),
            slice::from_raw_parts(plus, n - 1),
            slice::from_raw_parts(minus, n - 1),
            slice::from_raw_parts_mut(out_adx, n),
            slice::from_raw_parts_mut(out_plus, n),
            slice::from_raw_parts_mut(out_minus, n),
            slice::from_raw_parts_mut(out_dx, n),
        )
    };
    match wilder_adx(
        AdxInputs {
            ranges,
            plus,
            minus,
            period: period as usize,
            seeds: [seed_tr, seed_plus, seed_minus],
        },
        AdxOutputs {
            adx,
            plus: out_plus,
            minus: out_minus,
            dx,
        },
    ) {
        Ok(()) => 0,
        Err(status) => status,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(
        ranges: &[f64],
        plus: &[f64],
        minus: &[f64],
        period: usize,
        seeds: [f64; 3],
    ) -> Result<[Vec<f64>; 4], i64> {
        let n = ranges.len() + 1;
        let [mut adx, mut di_plus, mut di_minus, mut dx] =
            std::array::from_fn(|_| vec![f64::NAN; n]);
        wilder_adx(
            AdxInputs {
                ranges,
                plus,
                minus,
                period,
                seeds,
            },
            AdxOutputs {
                adx: &mut adx,
                plus: &mut di_plus,
                minus: &mut di_minus,
                dx: &mut dx,
            },
        )?;
        Ok([adx, di_plus, di_minus, dx])
    }

    #[test]
    fn monotonic_moves_preserve_warmup_and_full_direction() {
        let result = run(&[1.0; 6], &[1.0; 6], &[0.0; 6], 2, [2.0, 2.0, 0.0]).unwrap();
        assert!(result[0][..3].iter().all(|x| x.is_nan()));
        assert!(result[0][3..].iter().all(|x| *x == 100.0));
        assert_eq!(result[1][2], 100.0);
        assert_eq!(result[2][2], 0.0);
    }

    #[test]
    fn flat_market_has_zero_direction_after_warmup() {
        let result = run(&[0.0; 8], &[0.0; 8], &[0.0; 8], 3, [0.0; 3]).unwrap();
        assert_eq!(result[0][5], 0.0);
        assert_eq!(result[1][3], 0.0);
        assert_eq!(result[2][3], 0.0);
        assert_eq!(result[3][3], 0.0);
    }

    #[test]
    fn invalid_lengths_and_periods_are_rejected() {
        assert_eq!(
            run(&[1.0; 4], &[1.0; 3], &[0.0; 4], 2, [2.0, 2.0, 0.0]).unwrap_err(),
            -1
        );
        assert_eq!(
            run(&[1.0; 4], &[1.0; 4], &[0.0; 4], 1, [1.0, 1.0, 0.0]).unwrap_err(),
            -2
        );
    }

    #[test]
    fn invalid_seeds_and_nonfinite_inputs_are_rejected() {
        assert_eq!(
            run(
                &[1.0; 4],
                &[1.0; 4],
                &[0.0; 4],
                2,
                [f64::INFINITY, 2.0, 0.0]
            )
            .unwrap_err(),
            -3
        );
        assert_eq!(
            run(&[f64::NAN; 4], &[1.0; 4], &[0.0; 4], 2, [2.0, 2.0, 0.0]).unwrap_err(),
            -4
        );
    }

    #[test]
    fn ffi_rejects_null_pointers_before_slice_creation() {
        // SAFETY: null pointers are intentionally rejected before dereferencing.
        let status = unsafe {
            axiom_wilder_adx(
                std::ptr::null(),
                std::ptr::null(),
                std::ptr::null(),
                5,
                2,
                2.0,
                2.0,
                0.0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        };
        assert_eq!(status, -8);
    }
}
