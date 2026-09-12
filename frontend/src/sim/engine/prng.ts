/**
 * Re-exports the loader's own mulberry32 PRNG (plan section 9: "reuse it, do not
 * write a second PRNG"). One seed reproduces a race exactly.
 */
export { mulberry32, randRange } from "@/components/loader/physics/prng";
