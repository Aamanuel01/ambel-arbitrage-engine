// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IFlashLoanSimpleReceiver} from "./interfaces/IFlashLoanSimpleReceiver.sol";
import {IPoolAddressesProvider} from "./interfaces/IPoolAddressesProvider.sol";
import {IPool} from "./interfaces/IPool.sol";
import {ISwapRouter} from "./interfaces/ISwapRouter.sol";
import {IUniswapV2Router02} from "./interfaces/IUniswapV2Router02.sol";

/// @title  ArbitrageExecutor
/// @notice Executes a flash-loan-funded arbitrage between Uniswap V3 and SushiSwap on Polygon.
///         Owner calls `requestFlashLoan`; Aave calls back `executeOperation` atomically.
///         If the trade is not profitable after fees the transaction reverts, protecting capital.
/// @dev    MVP — no live execution; use with a Foundry fork for simulation only.
contract ArbitrageExecutor is IFlashLoanSimpleReceiver, Ownable {
    using SafeERC20 for IERC20;

    // ── Immutables ────────────────────────────────────────────────────────────────
    IPool public immutable POOL;
    IPoolAddressesProvider public immutable ADDRESSES_PROVIDER;
    ISwapRouter public immutable UNISWAP_ROUTER;       // Uniswap V3 SwapRouter02
    IUniswapV2Router02 public immutable SUSHI_ROUTER;  // SushiSwap Router

    // ── Events ────────────────────────────────────────────────────────────────────
    event ArbitrageExecuted(
        address indexed asset,
        uint256 loanAmount,
        uint256 profit,
        address indexed dexBuy,
        address indexed dexSell
    );

    // ── Constructor ───────────────────────────────────────────────────────────────
    constructor(
        address _pool,
        address _addressesProvider,
        address _uniswapRouter,
        address _sushiRouter
    ) Ownable(msg.sender) {
        POOL = IPool(_pool);
        ADDRESSES_PROVIDER = IPoolAddressesProvider(_addressesProvider);
        UNISWAP_ROUTER = ISwapRouter(_uniswapRouter);
        SUSHI_ROUTER = IUniswapV2Router02(_sushiRouter);
    }

    // ── Owner entry-point ─────────────────────────────────────────────────────────

    /// @notice Initiates a flash loan from Aave V3 and triggers the arbitrage.
    /// @param asset     Token to borrow (e.g. USDC).
    /// @param amount    Amount to borrow (in asset's native decimals).
    /// @param params    ABI-encoded trade parameters — see `executeOperation`.
    function requestFlashLoan(
        address asset,
        uint256 amount,
        bytes calldata params
    ) external onlyOwner {
        POOL.flashLoanSimple(
            address(this), // receiver
            asset,
            amount,
            params,
            0 // referral code
        );
    }

    // ── Aave callback ─────────────────────────────────────────────────────────────

    /// @notice Called by Aave after the flash loan is transferred to this contract.
    /// @dev    params encodes: (address tokenB, address dexA, address dexB,
    ///                          uint256 minProfit, uint24 feeA, uint24 feeB, bool uniFirst)
    ///         dexA is the cheaper exchange (buy leg), dexB is the expensive one (sell leg).
    ///         uniFirst = true  → buy on Uniswap V3 first, sell on SushiSwap
    ///         uniFirst = false → buy on SushiSwap first, sell on Uniswap V3
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        require(msg.sender == address(POOL), "ArbitrageExecutor: caller is not Aave Pool");
        require(initiator == address(this), "ArbitrageExecutor: unexpected initiator");

        (
            address tokenB,
            ,   // dexA address — routing is handled via uniFirst flag
            ,   // dexB address
            uint256 minProfit,
            uint24 feeA,
            uint24 feeB,
            bool uniFirst
        ) = abi.decode(params, (address, address, address, uint256, uint24, uint24, bool));

        uint256 repayAmount = amount + premium;
        uint256 tokenBReceived;

        if (uniFirst) {
            // ── Leg 1: Buy tokenB on Uniswap V3 ──────────────────────────────────
            tokenBReceived = _swapOnUniswapV3(asset, tokenB, amount, feeA, 0);
            // ── Leg 2: Sell tokenB on SushiSwap ──────────────────────────────────
            _swapOnSushiSwap(tokenB, asset, tokenBReceived, repayAmount);
        } else {
            // ── Leg 1: Buy tokenB on SushiSwap ───────────────────────────────────
            tokenBReceived = _swapOnSushiSwap(asset, tokenB, amount, 0);
            // ── Leg 2: Sell tokenB on Uniswap V3 ─────────────────────────────────
            _swapOnUniswapV3(tokenB, asset, tokenBReceived, feeB, repayAmount);
        }

        uint256 balance = IERC20(asset).balanceOf(address(this));
        require(balance >= repayAmount, "ArbitrageExecutor: insufficient balance to repay");

        uint256 profit = balance - repayAmount;
        require(profit >= minProfit, "ArbitrageExecutor: profit below minimum threshold");

        // ── Repay Aave ────────────────────────────────────────────────────────────
        IERC20(asset).safeApprove(address(POOL), 0);
        IERC20(asset).safeApprove(address(POOL), repayAmount);

        // ── Transfer profit to owner ──────────────────────────────────────────────
        if (profit > 0) {
            IERC20(asset).safeTransfer(owner(), profit);
        }

        emit ArbitrageExecuted(asset, amount, profit, address(UNISWAP_ROUTER), address(SUSHI_ROUTER));
        return true;
    }

    // ── Internal swap helpers ─────────────────────────────────────────────────────

    /// @dev Swap `amountIn` of `tokenIn` for `tokenOut` on Uniswap V3.
    ///      If `amountOutMinimum` is 0 the call accepts any output (simulation only).
    function _swapOnUniswapV3(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint24 fee,
        uint256 amountOutMinimum
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).safeApprove(address(UNISWAP_ROUTER), 0);
        IERC20(tokenIn).safeApprove(address(UNISWAP_ROUTER), amountIn);

        ISwapRouter.ExactInputSingleParams memory swapParams = ISwapRouter.ExactInputSingleParams({
            tokenIn: tokenIn,
            tokenOut: tokenOut,
            fee: fee,
            recipient: address(this),
            deadline: block.timestamp,
            amountIn: amountIn,
            amountOutMinimum: amountOutMinimum,
            sqrtPriceLimitX96: 0
        });

        amountOut = UNISWAP_ROUTER.exactInputSingle(swapParams);
    }

    /// @dev Swap `amountIn` of `tokenIn` for `tokenOut` on SushiSwap (Uniswap V2-style).
    ///      If `amountOutMinimum` is 0 the call accepts any output (simulation only).
    function _swapOnSushiSwap(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOutMinimum
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).safeApprove(address(SUSHI_ROUTER), 0);
        IERC20(tokenIn).safeApprove(address(SUSHI_ROUTER), amountIn);

        address[] memory path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;

        uint256[] memory amounts = SUSHI_ROUTER.swapExactTokensForTokens(
            amountIn,
            amountOutMinimum,
            path,
            address(this),
            block.timestamp
        );

        amountOut = amounts[amounts.length - 1];
    }

    // ── Admin ─────────────────────────────────────────────────────────────────────

    /// @notice Rescue any ERC-20 tokens accidentally sent to this contract.
    function withdraw(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        require(balance > 0, "ArbitrageExecutor: nothing to withdraw");
        IERC20(token).safeTransfer(owner(), balance);
    }

    /// @notice Rescue native MATIC accidentally sent to this contract.
    function withdrawMatic() external onlyOwner {
        uint256 balance = address(this).balance;
        require(balance > 0, "ArbitrageExecutor: no MATIC to withdraw");
        payable(owner()).transfer(balance);
    }

    receive() external payable {}
}
