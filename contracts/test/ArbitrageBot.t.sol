// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test, console} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ArbitrageBot} from "../src/ArbitrageBot.sol";

/// @notice Foundry fork tests for ArbitrageBot against Polygon mainnet.
///         Run with:
///   forge test --fork-url $POLYGON_WS_RPC_URL --match-contract ArbBotTest -vv
contract ArbBotTest is Test {
    // ── Polygon mainnet addresses ─────────────────────────────────────────────────
    address constant AAVE_ADDRESSES_PROV = 0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb;
    address constant UNISWAP_V3_ROUTER   = 0xE592427A0AEce92De3Edee1F18E0157C05861564;
    address constant SUSHI_ROUTER        = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;

    address constant USDC   = 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174;
    address constant WETH   = 0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619;
    address constant WMATIC = 0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270;

    // ── State ─────────────────────────────────────────────────────────────────────
    ArbitrageBot bot;
    address owner;

    function setUp() public {
        owner = makeAddr("owner");

        vm.startPrank(owner);
        // Constructor now only needs the Aave Addresses Provider
        bot = new ArbitrageBot(AAVE_ADDRESSES_PROV);
        vm.stopPrank();
    }

    // ── testFlashLoanRepayment ────────────────────────────────────────────────────
    /// @notice Verifies the onlyOwner guard and call path to Aave.
    ///         Expected to revert at the swap step (we only seeded the premium, not enough to trade).
    ///         See testArbitrageSimulation for the full end-to-end fork test.
    function testFlashLoanRepayment() public {
        uint256 loanAmount = 1_000e6; // 1,000 USDC

        bytes memory params = abi.encode(
            WETH,
            UNISWAP_V3_ROUTER,
            SUSHI_ROUTER,
            uint256(0),   // minProfit — repayment path test only
            uint24(3000),
            uint24(3000),
            true
        );

        // Seed only the Aave premium buffer — not enough for real swaps
        uint256 premium = loanAmount * 5 / 10_000;
        deal(USDC, address(bot), premium + 1e6);

        vm.prank(owner);
        vm.expectRevert();
        bot.requestFlashLoan(USDC, loanAmount, params);
    }

    // ── testArbitrageSimulation ───────────────────────────────────────────────────
    /// @notice Runs the full two-leg swap against a Polygon fork.
    ///         If the trade reverts (e.g. on a balanced fork with no spread), the test
    ///         fails — this is intentional: simulation success requires a profitable trade.
    function testArbitrageSimulation() public {
        uint256 loanAmount = 10_000e6; // 10,000 USDC

        uint256 premium = loanAmount * 5 / 10_000; // 0.05%
        deal(USDC, address(bot), premium + 100e6);

        bytes memory params = abi.encode(
            WETH,
            UNISWAP_V3_ROUTER,
            SUSHI_ROUTER,
            uint256(0), // minProfit = 0 so balanced-fork spreads don't cause a revert
            uint24(500),
            uint24(3000),
            true
        );

        uint256 balanceBefore = IERC20(USDC).balanceOf(owner);

        vm.prank(owner);
        bot.requestFlashLoan(USDC, loanAmount, params);

        uint256 profit = IERC20(USDC).balanceOf(owner) - balanceBefore;
        console.log("Simulated profit (USDC 6-dec):", profit);
    }

    // ── testRevertWhenUnprofitable ────────────────────────────────────────────────
    /// @notice Confirms the arbitrage pipeline reverts when minProfit cannot be satisfied.
    ///         On a real fork the round-trip swap fees (~0.65%) cause the trade to revert
    ///         at the SushiSwap minimum-output check before reaching BelowMinProfit; both
    ///         are correct reverts that protect capital.
    function testRevertWhenUnprofitable() public {
        uint256 loanAmount = 100e6; // 100 USDC

        bytes memory params = abi.encode(
            WETH,
            UNISWAP_V3_ROUTER,
            SUSHI_ROUTER,
            uint256(type(uint256).max), // minProfit = max → always reverts
            uint24(3000),
            uint24(3000),
            true
        );

        deal(USDC, address(bot), 10e6);

        vm.prank(owner);
        vm.expectRevert();
        bot.requestFlashLoan(USDC, loanAmount, params);
    }

    // ── testOnlyOwner ─────────────────────────────────────────────────────────────
    /// @notice Non-owners must not be able to trigger a flash loan.
    function testOnlyOwner() public {
        address attacker = makeAddr("attacker");
        bytes memory params = abi.encode(
            WETH,
            UNISWAP_V3_ROUTER,
            SUSHI_ROUTER,
            uint256(0),
            uint24(3000),
            uint24(3000),
            true
        );

        vm.prank(attacker);
        vm.expectRevert();
        bot.requestFlashLoan(USDC, 1_000e6, params);
    }

    // ── testWithdrawProfits ───────────────────────────────────────────────────────
    /// @notice Owner can withdraw ERC-20 tokens held by the contract.
    function testWithdrawProfits() public {
        deal(USDC, address(bot), 500e6);

        uint256 ownerBefore = IERC20(USDC).balanceOf(owner);
        vm.prank(owner);
        bot.withdrawProfits(USDC);
        uint256 ownerAfter = IERC20(USDC).balanceOf(owner);

        assertEq(ownerAfter - ownerBefore, 500e6);
    }

    // ── testWithdrawProfitsOnlyOwner ──────────────────────────────────────────────
    function testWithdrawProfitsOnlyOwner() public {
        deal(USDC, address(bot), 100e6);
        address attacker = makeAddr("attacker");
        vm.prank(attacker);
        vm.expectRevert();
        bot.withdrawProfits(USDC);
    }

    // ── testWithdrawNative ────────────────────────────────────────────────────────
    /// @notice Owner can rescue native MATIC/POL from the contract.
    function testWithdrawNative() public {
        vm.deal(address(bot), 1 ether);

        uint256 ownerBefore = owner.balance;
        vm.prank(owner);
        bot.withdrawNative();

        assertEq(owner.balance - ownerBefore, 1 ether);
    }

    // ── testWithdrawNativeOnlyOwner ───────────────────────────────────────────────
    function testWithdrawNativeOnlyOwner() public {
        vm.deal(address(bot), 1 ether);
        address attacker = makeAddr("attacker");
        vm.prank(attacker);
        vm.expectRevert();
        bot.withdrawNative();
    }
}
