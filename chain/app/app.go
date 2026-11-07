// Package app wires the AURIX Cosmos SDK application.
//
// Layer 1 (x/aurixbank in the design docs) is realised by the standard
// x/auth (accounts, sequences = nonces, signatures, fees) and x/bank
// (uaur balances, transfers) modules. There is no mint module: the supply is
// fixed at genesis (invariant 1). Layer 2 is the custom x/smartmoney module.
package app

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"

	reflectionv1 "cosmossdk.io/api/cosmos/reflection/v1"
	"cosmossdk.io/log"
	storetypes "cosmossdk.io/store/types"
	"cosmossdk.io/x/evidence"
	evidencekeeper "cosmossdk.io/x/evidence/keeper"
	evidencetypes "cosmossdk.io/x/evidence/types"
	"cosmossdk.io/x/tx/signing"
	abci "github.com/cometbft/cometbft/abci/types"
	dbm "github.com/cosmos/cosmos-db"
	"github.com/cosmos/cosmos-sdk/baseapp"
	"github.com/cosmos/cosmos-sdk/client"
	"github.com/cosmos/cosmos-sdk/client/grpc/cmtservice"
	nodeservice "github.com/cosmos/cosmos-sdk/client/grpc/node"
	"github.com/cosmos/cosmos-sdk/codec"
	"github.com/cosmos/cosmos-sdk/codec/address"
	"github.com/cosmos/cosmos-sdk/codec/types"
	"github.com/cosmos/cosmos-sdk/runtime"
	runtimeservices "github.com/cosmos/cosmos-sdk/runtime/services"
	"github.com/cosmos/cosmos-sdk/server/api"
	"github.com/cosmos/cosmos-sdk/server/config"
	servertypes "github.com/cosmos/cosmos-sdk/server/types"
	"github.com/cosmos/cosmos-sdk/std"
	sdk "github.com/cosmos/cosmos-sdk/types"
	"github.com/cosmos/cosmos-sdk/types/module"
	"github.com/cosmos/cosmos-sdk/x/auth"
	"github.com/cosmos/cosmos-sdk/x/auth/ante"
	authcodec "github.com/cosmos/cosmos-sdk/x/auth/codec"
	authkeeper "github.com/cosmos/cosmos-sdk/x/auth/keeper"
	authtx "github.com/cosmos/cosmos-sdk/x/auth/tx"
	authtypes "github.com/cosmos/cosmos-sdk/x/auth/types"
	"github.com/cosmos/cosmos-sdk/x/bank"
	bankkeeper "github.com/cosmos/cosmos-sdk/x/bank/keeper"
	banktypes "github.com/cosmos/cosmos-sdk/x/bank/types"
	"github.com/cosmos/cosmos-sdk/x/consensus"
	consensuskeeper "github.com/cosmos/cosmos-sdk/x/consensus/keeper"
	consensustypes "github.com/cosmos/cosmos-sdk/x/consensus/types"
	distr "github.com/cosmos/cosmos-sdk/x/distribution"
	distrkeeper "github.com/cosmos/cosmos-sdk/x/distribution/keeper"
	distrtypes "github.com/cosmos/cosmos-sdk/x/distribution/types"
	"github.com/cosmos/cosmos-sdk/x/genutil"
	genutiltypes "github.com/cosmos/cosmos-sdk/x/genutil/types"
	govtypes "github.com/cosmos/cosmos-sdk/x/gov/types"
	"github.com/cosmos/cosmos-sdk/x/slashing"
	slashingkeeper "github.com/cosmos/cosmos-sdk/x/slashing/keeper"
	slashingtypes "github.com/cosmos/cosmos-sdk/x/slashing/types"
	"github.com/cosmos/cosmos-sdk/x/staking"
	stakingkeeper "github.com/cosmos/cosmos-sdk/x/staking/keeper"
	stakingtypes "github.com/cosmos/cosmos-sdk/x/staking/types"
	"github.com/cosmos/gogoproto/proto"

	"github.com/joeylukoji/aurix/chain/x/smartmoney"
	smkeeper "github.com/joeylukoji/aurix/chain/x/smartmoney/keeper"
	smtypes "github.com/joeylukoji/aurix/chain/x/smartmoney/types"
)

const (
	Name         = "aurix"
	Bech32Prefix = "aur"
	BondDenom    = smtypes.Denom
)

var (
	DefaultNodeHome string
	maccPerms       = map[string][]string{
		authtypes.FeeCollectorName:     nil,
		distrtypes.ModuleName:          nil,
		stakingtypes.BondedPoolName:    {authtypes.Burner, authtypes.Staking},
		stakingtypes.NotBondedPoolName: {authtypes.Burner, authtypes.Staking},
		smtypes.ModuleName:             nil,
	}
	_ servertypes.Application = (*App)(nil)
)

func init() {
	home, _ := os.UserHomeDir()
	DefaultNodeHome = filepath.Join(home, ".aurix")
}

// SetConfig sets the bech32 prefixes (aur1..., aurvaloper1...) globally.
func SetConfig() {
	cfg := sdk.GetConfig()
	cfg.SetBech32PrefixForAccount(Bech32Prefix, Bech32Prefix+"pub")
	cfg.SetBech32PrefixForValidator(Bech32Prefix+"valoper", Bech32Prefix+"valoperpub")
	cfg.SetBech32PrefixForConsensusNode(Bech32Prefix+"valcons", Bech32Prefix+"valconspub")
	cfg.Seal()
}

type EncodingConfig struct {
	InterfaceRegistry types.InterfaceRegistry
	Codec             codec.Codec
	TxConfig          client.TxConfig
	Amino             *codec.LegacyAmino
}

func MakeEncodingConfig() EncodingConfig {
	ir, err := types.NewInterfaceRegistryWithOptions(types.InterfaceRegistryOptions{
		ProtoFiles: proto.HybridResolver,
		SigningOptions: signing.Options{
			AddressCodec:          address.Bech32Codec{Bech32Prefix: Bech32Prefix},
			ValidatorAddressCodec: address.Bech32Codec{Bech32Prefix: Bech32Prefix + "valoper"},
		},
	})
	if err != nil {
		panic(err)
	}
	cdc := codec.NewProtoCodec(ir)
	amino := codec.NewLegacyAmino()
	std.RegisterLegacyAminoCodec(amino)
	std.RegisterInterfaces(ir)
	ModuleBasics.RegisterLegacyAminoCodec(amino)
	ModuleBasics.RegisterInterfaces(ir)
	return EncodingConfig{ir, cdc, authtx.NewTxConfig(cdc, authtx.DefaultSignModes), amino}
}

// ModuleBasics is used for codec registration and genesis defaults.
var ModuleBasics = module.NewBasicManager(
	auth.AppModuleBasic{},
	genutil.NewAppModuleBasic(genutiltypes.DefaultMessageValidator),
	bank.AppModuleBasic{},
	staking.AppModuleBasic{},
	distr.AppModuleBasic{},
	slashing.AppModuleBasic{},
	evidence.AppModuleBasic{},
	consensus.AppModuleBasic{},
	smartmoney.AppModule{},
)

type App struct {
	*baseapp.BaseApp
	cdc               codec.Codec
	interfaceRegistry types.InterfaceRegistry
	txConfig          client.TxConfig

	AccountKeeper    authkeeper.AccountKeeper
	BankKeeper       bankkeeper.BaseKeeper
	StakingKeeper    *stakingkeeper.Keeper
	DistrKeeper      distrkeeper.Keeper
	SlashingKeeper   slashingkeeper.Keeper
	EvidenceKeeper   evidencekeeper.Keeper
	ConsensusKeeper  consensuskeeper.Keeper
	SmartMoneyKeeper smkeeper.Keeper

	ModuleManager *module.Manager
}

func New(logger log.Logger, db dbm.DB, traceStore io.Writer, loadLatest bool,
	_ servertypes.AppOptions, baseAppOptions ...func(*baseapp.BaseApp)) *App {
	enc := MakeEncodingConfig()
	bApp := baseapp.NewBaseApp(Name, logger, db, enc.TxConfig.TxDecoder(), baseAppOptions...)
	bApp.SetCommitMultiStoreTracer(traceStore)
	bApp.SetInterfaceRegistry(enc.InterfaceRegistry)
	bApp.SetTxEncoder(enc.TxConfig.TxEncoder())

	keys := storetypes.NewKVStoreKeys(authtypes.StoreKey, banktypes.StoreKey, stakingtypes.StoreKey,
		consensustypes.StoreKey, distrtypes.StoreKey, slashingtypes.StoreKey, evidencetypes.StoreKey, smtypes.StoreKey)
	authority := authtypes.NewModuleAddress(govtypes.ModuleName).String()

	app := &App{BaseApp: bApp, cdc: enc.Codec, interfaceRegistry: enc.InterfaceRegistry, txConfig: enc.TxConfig}
	app.ConsensusKeeper = consensuskeeper.NewKeeper(enc.Codec, runtime.NewKVStoreService(keys[consensustypes.StoreKey]),
		authority, runtime.EventService{})
	bApp.SetParamStore(app.ConsensusKeeper.ParamsStore)

	app.AccountKeeper = authkeeper.NewAccountKeeper(enc.Codec, runtime.NewKVStoreService(keys[authtypes.StoreKey]),
		authtypes.ProtoBaseAccount, maccPerms, authcodec.NewBech32Codec(Bech32Prefix), Bech32Prefix, authority)
	blocked := map[string]bool{}
	for acc := range maccPerms {
		blocked[authtypes.NewModuleAddress(acc).String()] = true
	}
	app.BankKeeper = bankkeeper.NewBaseKeeper(enc.Codec, runtime.NewKVStoreService(keys[banktypes.StoreKey]),
		app.AccountKeeper, blocked, authority, logger)
	app.StakingKeeper = stakingkeeper.NewKeeper(enc.Codec, runtime.NewKVStoreService(keys[stakingtypes.StoreKey]),
		app.AccountKeeper, app.BankKeeper, authority,
		authcodec.NewBech32Codec(Bech32Prefix+"valoper"), authcodec.NewBech32Codec(Bech32Prefix+"valcons"))
	app.DistrKeeper = distrkeeper.NewKeeper(enc.Codec, runtime.NewKVStoreService(keys[distrtypes.StoreKey]),
		app.AccountKeeper, app.BankKeeper, app.StakingKeeper, authtypes.FeeCollectorName, authority)
	app.SlashingKeeper = slashingkeeper.NewKeeper(enc.Codec, enc.Amino, runtime.NewKVStoreService(keys[slashingtypes.StoreKey]),
		app.StakingKeeper, authority)
	// Staking hooks: distribution tracks rewards, slashing tracks validator signing info.
	app.StakingKeeper.SetHooks(stakingtypes.NewMultiStakingHooks(app.DistrKeeper.Hooks(), app.SlashingKeeper.Hooks()))
	app.EvidenceKeeper = *evidencekeeper.NewKeeper(enc.Codec, runtime.NewKVStoreService(keys[evidencetypes.StoreKey]),
		app.StakingKeeper, app.SlashingKeeper, app.AccountKeeper.AddressCodec(), runtime.ProvideCometInfoService())
	app.SmartMoneyKeeper = smkeeper.NewKeeper(enc.Codec, runtime.NewKVStoreService(keys[smtypes.StoreKey]), app.BankKeeper)

	app.ModuleManager = module.NewManager(
		genutil.NewAppModule(app.AccountKeeper, app.StakingKeeper, app, enc.TxConfig),
		auth.NewAppModule(enc.Codec, app.AccountKeeper, nil, nil),
		bank.NewAppModule(enc.Codec, app.BankKeeper, app.AccountKeeper, nil),
		staking.NewAppModule(enc.Codec, app.StakingKeeper, app.AccountKeeper, app.BankKeeper, nil),
		distr.NewAppModule(enc.Codec, app.DistrKeeper, app.AccountKeeper, app.BankKeeper, app.StakingKeeper, nil),
		slashing.NewAppModule(enc.Codec, app.SlashingKeeper, app.AccountKeeper, app.BankKeeper, app.StakingKeeper, nil, enc.InterfaceRegistry),
		evidence.NewAppModule(app.EvidenceKeeper),
		consensus.NewAppModule(enc.Codec, app.ConsensusKeeper),
		smartmoney.NewAppModule(enc.Codec, app.SmartMoneyKeeper),
	)
	// distribution must run before slashing (rewards for the previous block), slashing
	// before evidence and staking (downtime jailing uses the last commit).
	app.ModuleManager.SetOrderBeginBlockers(distrtypes.ModuleName, slashingtypes.ModuleName,
		evidencetypes.ModuleName, stakingtypes.ModuleName)
	app.ModuleManager.SetOrderEndBlockers(stakingtypes.ModuleName)
	app.ModuleManager.SetOrderInitGenesis(authtypes.ModuleName, banktypes.ModuleName, distrtypes.ModuleName,
		stakingtypes.ModuleName, slashingtypes.ModuleName, evidencetypes.ModuleName,
		smtypes.ModuleName, genutiltypes.ModuleName, consensustypes.ModuleName)
	app.ModuleManager.SetOrderExportGenesis(consensustypes.ModuleName, authtypes.ModuleName, banktypes.ModuleName,
		distrtypes.ModuleName, stakingtypes.ModuleName, slashingtypes.ModuleName, evidencetypes.ModuleName,
		smtypes.ModuleName, genutiltypes.ModuleName)

	cfg := module.NewConfigurator(enc.Codec, app.MsgServiceRouter(), app.GRPCQueryRouter())
	if err := app.ModuleManager.RegisterServices(cfg); err != nil {
		panic(err)
	}
	reflectionSvc, err := runtimeservices.NewReflectionService()
	if err != nil {
		panic(err)
	}
	reflectionv1.RegisterReflectionServiceServer(app.GRPCQueryRouter(), reflectionSvc)

	app.MountKVStores(keys)
	app.SetInitChainer(app.InitChainer)
	app.SetBeginBlocker(func(ctx sdk.Context) (sdk.BeginBlock, error) { return app.ModuleManager.BeginBlock(ctx) })
	app.SetEndBlocker(func(ctx sdk.Context) (sdk.EndBlock, error) { return app.ModuleManager.EndBlock(ctx) })

	anteHandler, err := ante.NewAnteHandler(ante.HandlerOptions{
		AccountKeeper:   app.AccountKeeper,
		BankKeeper:      app.BankKeeper,
		SignModeHandler: enc.TxConfig.SignModeHandler(),
		SigGasConsumer:  ante.DefaultSigVerificationGasConsumer,
	})
	if err != nil {
		panic(err)
	}
	app.SetAnteHandler(anteHandler)

	if loadLatest {
		if err := app.LoadLatestVersion(); err != nil {
			panic(err)
		}
	}
	return app
}

func (app *App) InitChainer(ctx sdk.Context, req *abci.RequestInitChain) (*abci.ResponseInitChain, error) {
	var gs map[string]json.RawMessage
	if err := json.Unmarshal(req.AppStateBytes, &gs); err != nil {
		return nil, err
	}
	return app.ModuleManager.InitGenesis(ctx, app.cdc, gs)
}

func (app *App) AppCodec() codec.Codec                      { return app.cdc }
func (app *App) InterfaceRegistry() types.InterfaceRegistry { return app.interfaceRegistry }
func (app *App) TxConfig() client.TxConfig                  { return app.txConfig }

func (app *App) RegisterAPIRoutes(apiSvr *api.Server, _ config.APIConfig) {
	clientCtx := apiSvr.ClientCtx
	authtx.RegisterGRPCGatewayRoutes(clientCtx, apiSvr.GRPCGatewayRouter)
	cmtservice.RegisterGRPCGatewayRoutes(clientCtx, apiSvr.GRPCGatewayRouter)
	nodeservice.RegisterGRPCGatewayRoutes(clientCtx, apiSvr.GRPCGatewayRouter)
	ModuleBasics.RegisterGRPCGatewayRoutes(clientCtx, apiSvr.GRPCGatewayRouter)
}

func (app *App) RegisterTxService(clientCtx client.Context) {
	authtx.RegisterTxService(app.GRPCQueryRouter(), clientCtx, app.Simulate, app.interfaceRegistry)
}

func (app *App) RegisterTendermintService(clientCtx client.Context) {
	cmtservice.RegisterTendermintService(clientCtx, app.GRPCQueryRouter(), app.interfaceRegistry, app.Query)
}

func (app *App) RegisterNodeService(clientCtx client.Context, cfg config.Config) {
	nodeservice.RegisterNodeService(clientCtx, app.GRPCQueryRouter(), cfg)
}

// ExportAppStateAndValidators exports the current state as genesis (zero-height export not supported).
func (app *App) ExportAppStateAndValidators(forZeroHeight bool, _, modulesToExport []string) (servertypes.ExportedApp, error) {
	if forZeroHeight {
		return servertypes.ExportedApp{}, fmt.Errorf("zero-height export is not supported")
	}
	ctx := app.NewContext(true)
	gs, err := app.ModuleManager.ExportGenesisForModules(ctx, app.cdc, modulesToExport)
	if err != nil {
		return servertypes.ExportedApp{}, err
	}
	appState, err := json.MarshalIndent(gs, "", "  ")
	if err != nil {
		return servertypes.ExportedApp{}, err
	}
	validators, err := staking.WriteValidators(ctx, app.StakingKeeper)
	return servertypes.ExportedApp{AppState: appState, Validators: validators, Height: app.LastBlockHeight(),
		ConsensusParams: app.GetConsensusParams(ctx)}, err
}

// LoadHeight loads a particular height.
func (app *App) LoadHeight(height int64) error { return app.LoadVersion(height) }
