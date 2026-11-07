package types

import errorsmod "cosmossdk.io/errors"

var (
	ErrInvalidAmount   = errorsmod.Register(ModuleName, 2, "invalid amount")
	ErrInvalidDeadline = errorsmod.Register(ModuleName, 3, "deadline must be above current height")
	ErrEscrowNotFound  = errorsmod.Register(ModuleName, 4, "escrow not found")
	ErrNotLocked       = errorsmod.Register(ModuleName, 5, "escrow is not LOCKED")
	ErrUnauthorized    = errorsmod.Register(ModuleName, 6, "unauthorized")
	ErrDeadlineNotMet  = errorsmod.Register(ModuleName, 7, "deadline not reached")
	ErrInvalidHash     = errorsmod.Register(ModuleName, 8, "hash must be 32 bytes hex")
	ErrDuplicateProof  = errorsmod.Register(ModuleName, 9, "proof already anchored")

	ErrDuplicateAttestation = errorsmod.Register(ModuleName, 10, "signer already attested this proof")
	ErrProofMismatch        = errorsmod.Register(ModuleName, 11, "external_ref/capture_ref differ from the existing proof")
	ErrInvalidAuthority     = errorsmod.Register(ModuleName, 12, "invalid authority")
	ErrInvalidParams        = errorsmod.Register(ModuleName, 13, "invalid params")
)
