package types

import (
	"github.com/cosmos/cosmos-sdk/codec"
	codectypes "github.com/cosmos/cosmos-sdk/codec/types"
	sdk "github.com/cosmos/cosmos-sdk/types"
	"github.com/cosmos/cosmos-sdk/types/msgservice"
)

func RegisterLegacyAminoCodec(cdc *codec.LegacyAmino) {
	cdc.RegisterConcrete(&MsgCreateEscrow{}, "aurix/MsgCreateEscrow", nil)
	cdc.RegisterConcrete(&MsgReleaseEscrow{}, "aurix/MsgReleaseEscrow", nil)
	cdc.RegisterConcrete(&MsgRefundEscrow{}, "aurix/MsgRefundEscrow", nil)
	cdc.RegisterConcrete(&MsgAnchorProof{}, "aurix/MsgAnchorProof", nil)
}

func RegisterInterfaces(registry codectypes.InterfaceRegistry) {
	registry.RegisterImplementations((*sdk.Msg)(nil),
		&MsgCreateEscrow{}, &MsgReleaseEscrow{}, &MsgRefundEscrow{}, &MsgAnchorProof{})
	msgservice.RegisterMsgServiceDesc(registry, &_Msg_serviceDesc)
}
